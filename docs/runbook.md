# Operator Runbook

## Routine operations

**Run ingestion (all six sources):**
```bash
python3 -m northwind.cli.main ingest all --input-dir seed/baseline
```
Safe to re-run any time; idempotent by design (see README for what that means concretely).

**Check system health:**
```bash
python3 -m northwind.cli.main health
```
Or `GET /health` on the API. Green means every source has a recent successful ingestion
within the freshness SLA; a stale source shows up here before it can silently affect a
campaign (see Failure 3 below).

**Review the reconciliation report** (always do this after ingestion, not just when something
looks wrong):
```bash
python3 -m northwind.cli.main reconcile
```

**Run the findings catalog:**
```bash
python3 -m northwind.cli.main findings
```
Safe to re-run; disposed (accepted/suppressed) findings keep their disposition across reruns.

**Launch a review campaign:**
```bash
python3 -m northwind.cli.main campaign create "Q3 Salesforce Review" --application Salesforce
```
Will refuse if the source is stale (see Failure 3).

**Trigger a JML event manually** (normally driven by the Zoho webhook):
```bash
python3 -m northwind.cli.main jml leaver "Jane Smith" --trigger manual-2026-09-15 --mode dry_run
```
Always dry-run first for anything you're not certain about; dry-run never modifies state.

## The five most likely failures, and how to recover

### 1. Ingestion fails partway through (`status: failed`)

**Symptom:** `northwind health` shows a source as `failed` rather than `succeeded`.
**Diagnosis:** `northwind reconcile <run-id>` (the run id is in the health output) shows the
error. Most commonly a malformed source file (check `error` field on the `IngestionRun` row).
**Recovery:** Fix the source file, re-run `ingest all` for that source. Re-running is always
safe — already-processed rows are recognized by content hash and not duplicated.

### 2. A JML workflow is `partially_failed`

**Symptom:** `northwind jml <action> <name> --trigger <id>` reports `partially_failed`.
**Diagnosis:** Inspect the printed step list — one or more steps show `status: failed` with an
error message (usually a simulated or real connector timeout).
**Recovery:** Re-run the exact same command with the exact same `--trigger` value once the
underlying connector issue is resolved. The workflow resumes from where it left off — already-
succeeded steps are not retried, only failed ones. Do not use a new `--trigger` value for a
retry; that starts a fresh, unrelated workflow run instead of resuming.

### 3. Campaign creation is refused ("source is stale")

**Symptom:** `northwind campaign create ...` raises an error citing source freshness.
**Diagnosis:** The named application's source hasn't completed a successful ingestion within
`INGESTION_FRESHNESS_SLA_HOURS` (default 24h).
**Recovery:** Run `ingest all` (or just that source's connector) and retry. If you genuinely
need to launch against a known-stale source (e.g., testing), this requires an explicit code-
level override (`allow_stale=True`) — there is deliberately no CLI flag for this, so that
overriding staleness is always a conscious, logged engineering decision, not an accidental
default.

### 4. A finding you expected to see is missing

**Symptom:** You know a condition should trigger a finding (e.g., a specific terminated
identity with active access) but `northwind findings --rule <RULE_CODE>` doesn't show it.
**Diagnosis:** Most commonly, the finding has an active disposition (`FindingDisposition`)
suppressing or accepting it from a previous run — check `finding_disposition` for a matching
`finding_key`. Second most common: the underlying account is disabled (findings that key on
"active access" specifically check `Account.enabled`).
**Recovery:** If the disposition is stale (expired justification), remove or let it expire and
re-run `northwind findings`.

### 5. Correlation quarantined more (or fewer) records than expected after a source update

**Symptom:** `northwind reconcile` shows a quarantine count that looks off versus your mental
model of the source data.
**Diagnosis:** Use `northwind diff <run-a> <run-b>` to compare the two ingestion runs' row
counts. Then check the `reconciliation_metric` table's reason-code breakdown for the newer
run — a spike in one reason code (e.g., `CORRELATION_AMBIGUOUS`) usually means a name
collision or a genuinely new ambiguous case, not a bug. See `docs/troubleshooting.md` for the
decision tree.

## What NOT to do

- Don't truncate the `source_record` or `quarantine_record` tables to "clean up" — they are
  the audit trail. If quarantine volume is a problem, resolve the underlying data, don't erase
  the record of it having existed.
- Don't retry a `partially_failed` JML run with a new trigger id (see Failure 2) — this
  creates two workflow runs for one event, which will confuse the audit trail even though it
  won't corrupt any data (both runs are individually idempotent and safe).
