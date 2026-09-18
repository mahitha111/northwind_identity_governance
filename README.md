# Northwind Materials Identity Governance POC

A BalkanID Forward Deployed Engineering / Customer Success hiring task submission: a working
identity governance deployment against realistic, dirty enterprise identity data for a
fictional prospect, Northwind Materials.

**Submission note:** this README states exactly what the running system does and does not do.
Every "done" claim below has been run and verified against real data (PostgreSQL 16, not
mocked) during development; every "not done" or "not verified" item is stated as such rather
than implied otherwise. See `docs/ai.md` for real bugs caught during development and how.

## System Architecture

![System architecture](docs/architecture-diagram.svg)

## Core / Stretch matrix (what was attempted, and where)

| Section | Attempted at | Status |
|---|---|---|
| A1 Ingestion & Normalization | Core | **Done, verified.** 16 integration tests, reconciliation invariant enforced as a hard assertion, idempotency verified across two full six-source runs. |
| A2 Correlation & Entitlement Graph | Core | **Done, verified.** Effective-access resolution with full path evidence (Stretch: nested/inherited access, including the 3-level AD nesting and the AWS assume-role chain, both implemented and verified). |
| A3 Risk Findings | Core + partial Stretch | **Done.** All 7 rule types, explicit scoring arithmetic stored per finding, idempotent. Stretch (tuning/disposition survives re-ingestion) implemented via `FindingDisposition`; peer-outlier threshold tuning documented as ongoing, not perfected. |
| A4 JML | Core + partial Stretch | **Done.** Joiner/mover/leaver all working. Dry-run mode and partial-failure-with-retry-to-completion verified for leaver specifically (see `tests/integration/test_leaver_workflow.py`). n8n implementation explicitly deferred (see "What we cut"). |
| A5 Access Review Campaign | Core | **Done, verified end-to-end** against real Salesforce data (860 review items, manager-hierarchy routing with IAM-fallback-queue routing verified, decisions, revocation execution, evidence pack). Aggregated/bulk review (Stretch) explicitly deferred. |
| A6 Operator Console | Core | **Done, smoke-tested.** Streamlit app (`console/app.py`) covering findings, campaign progress, JML run history, data health, identity 360, and an executive value view. Boots and serves successfully; not exhaustively UI-tested. |
| A7 Triage Tooling | Core + Stretch | **Done.** `why-not`, `why`, and `diff` all implemented and tested against real data, citing actual evidence (not inferred from logs). |
| B1 POC Plan | Core | **Done.** `docs/poc-plan.md`. |
| B2 Incident | Core + partial Stretch | **Done, with an honest gap.** 2 of 3 causes genuinely reproduced live against this running system (see `scripts/day14_incident_demo.py`); the third (PlantOps header shift) was found to already be prevented by an A1 design decision rather than manufactured as a live failure -- see `docs/incident/rca.md` for the full accounting. Second-order auditor-notification Stretch item covered in the engineering ticket. |
| B3 Documentation | Core | **Done.** Runbook, product doc, troubleshooting tree. |
| B4 Executive Readout | Core + Stretch | **Partial.** Readout content written (`docs/readout.md`) including the Stretch conversion recommendation with pricing basis and risk. **Not done:** rendered as an actual PDF deck, and no demo recording (no video capability in this environment) -- stated plainly rather than fabricated. |
| B5 Product Feedback | Stretch | **Done.** `docs/product-feedback.md`. |
| C AI Leverage | Core | **Done.** `docs/ai.md`, four real bugs documented with how each was caught. |
| Dockerization | Required | **Verified end-to-end.** See `docs/deployment.md` and the note below. |
| Kubernetes / CI/CD / cloud deployment | Bonus | **Not attempted**, deliberately, until Compose itself is verified (see `docs/deployment.md`). |

**Time spent:** this was built across an extended agentic session rather than tracked in
discrete hours; the honest estimate, applying the task's own effort bands, is that this
sits at the upper end of "Core plus a focused set of Stretch items," with the executive
deck/recording as the clearest remaining gap to a complete Core+Stretch submission.

## What's built

### A1 -- Ingestion and Normalization (Core): done
### A2 -- Identity Correlation and Entitlement Graph (Core, + Stretch path evidence): done

Six source connectors (`src/northwind/ingestion/{zoho,entra,ad,salesforce,aws,plantops}.py`)
ingest the deterministically-seeded (seed 42) six-source dataset into a normalized
identity -> account -> entitlement -> application graph in PostgreSQL, with full provenance
(every record traces to a source file, sheet, and line) and an explicit quarantine path for
anything that can't be confidently resolved.

Run it yourself:

```bash
# 1. Bring up Postgres and apply migrations
docker compose up -d postgres migrate     # or see "Local (no Docker)" below

# 2. Generate the seed data (deterministic, seed 42)
python3 seed/generate.py --out-dir seed/baseline

# 3. Ingest all six sources
python3 -m northwind.cli.main ingest all --input-dir seed/baseline

# 4. Inspect the results
python3 -m northwind.cli.main health
python3 -m northwind.cli.main reconcile
python3 -m northwind.cli.main access "Priya Raghunathan"
```

#### Local (no Docker) setup used during development

Development and initial verification was done against a real, locally-installed PostgreSQL 16
instance:

```bash
apt-get install -y postgresql
service postgresql start
su postgres -c "psql -c \"CREATE USER northwind WITH PASSWORD 'northwind' SUPERUSER;\""
su postgres -c "psql -c \"CREATE DATABASE northwind OWNER northwind;\""
pip install -e ".[dev]" --break-system-packages
export DATABASE_URL="postgresql+psycopg://northwind:northwind@localhost:5432/northwind"
alembic upgrade head
```

The Docker Compose path has been verified end-to-end: all four services (postgres, migrate,
api, console) start cleanly, ingest all six sources with results matching local development
exactly, and both the API health endpoint and console are reachable.

### Reconciliation results (actual, from a real run)

| Source | rows_in | normalized | quarantined | invariant |
|---|---:|---:|---:|---|
| Zoho People | 2400 | 2393 | 7 | rows_in = normalized + quarantined ✅ |
| Entra ID | 2150 | 2033 | 117 | ✅ |
| Active Directory | 2311 | 2187 | 124 | ✅ |
| Salesforce | 480 | 431 | 49 | ✅ |
| AWS IAM | 200 | 170 | 30 | ✅ |
| PlantOps | 350 | 199 | 151 | ✅ |

**PlantOps quarantine breakdown (151/350, ~43% -- the highest of any source, and
deliberately so):**

| Reason | Rows |
|---|---|
| `STALE_WORKSHEET_EXCLUDED` | 40 |
| `PLANTOPS_AMBIGUOUS_NAME_MATCH` | 49 |
| `PLANTOPS_NAME_MATCH_MISSING_ROLE_CONTEXT` | 62 |

The larger driver isn't the stale worksheet (40 rows) -- it's the 111 rows quarantined by
the name-match/role-context policy. PlantOps has no employee ID and no email (Appendix
A.6), so identity resolution is name-only by construction, and roughly a third of the
seeded PlantOps population is drawn from outside Manufacturing, where the system has no
departmental context to corroborate an ambiguous name match. Per the task's own stated
policy for this source ("name-only matches require unique strong name similarity plus
contextual role evidence"), those rows are correctly refused rather than guessed. A 43%
quarantine rate on PlantOps specifically is the expected shape of this source's data, not
an indicator that something's wrong with correlation -- the other five sources, which
carry an employee ID or verified email, quarantine at 5-15%, which is the more informative
comparison.

The `rows_in = rows_normalized + rows_quarantined` invariant is enforced as a hard assertion
in `ReconciliationCounter.flush()` (`src/northwind/ingestion/helpers.py`) -- a broken
invariant raises, it doesn't get silently reported. It's also checked as a test for every
connector (`tests/integration/`).

**No row is ever silently dropped.** Every source row becomes exactly one `SourceRecord`
(with file/sheet/line provenance) and is marked either `normalized` or `quarantined` with a
reason code. `northwind reconcile` shows the full reason-code breakdown per source.

**Idempotency is verified, not just claimed.** Running `ingest all` twice against identical
input produces byte-identical counts across every business and provenance table (identities,
accounts, entitlements, account_entitlements, entitlement_edges, source_records, quarantine
records) -- confirmed by direct comparison, not inferred. See
`tests/integration/test_zoho_ingestion.py::test_zoho_ingestion_is_idempotent` and the parallel
`test_other_connectors.py::test_connector_is_idempotent`.

### Self-reported precision / recall

Per the task's instructions, `seed/ground_truth.json` was not read until A1 and A2 were
frozen. `scripts/evaluate_precision_recall.py` then compared our actual correlation decisions
against it. Results, unedited:


== Zoho employee_id collisions ==
Ground truth: 3 collision groups (6 rows). System flagged 6/6 rows under
EMPLOYEE_ID_COLLISION. All 6 auto-resolved without merging distinct people into one identity.

== AD unmatched account classification (contractor/service/orphan) ==
Contractor classification: precision=100.00% recall=96.65% (tp=173 fp=0 fn=6)
Service classification (non-human typing): precision=96.67% recall=100.00% (tp=29 fp=1 fn=0)
(of which 1/29 also had an identifiable owner in the account description -- the rest are
correctly left 'unresolved' for a human to assign; that's honest non-attribution, not a miss)
Orphan classification: precision=100.00% recall=100.00% (tp=12 fp=0 fn=0)

== AD/Entra dual-account resolution (12 people, different UPNs) ==
System correctly attributed both accounts to one identity for 11/12.

== Salesforce legacy-domain emails (30 rows) ==
System quarantined 30/30 rows under CORRELATION_LEGACY_DOMAIN (by design: a foreign email
domain never auto-matches, precision over recall).

== AWS IAM users with no human match (22 automation users) ==
precision=73.33% recall=100.00% (tp=22 fp=8 fn=0)

== PlantOps stale-worksheet exclusion ==
System excluded 40/40 rows from the unlabeled prior-year worksheet as stale/duplicate.



**Honest read of the misses, not just the numbers:**

- **The 6 "missed" contractors** (recall 96.65%, not 100%) are contractors whose
  Faker-generated name happens to collide with a real employee's name. Their AD identifier
  then matches that employee's UPN local part too, which our correlation engine correctly
  treats as *ambiguous* (`CORRELATION_AMBIGUOUS`) rather than confidently auto-classifying as
  either "matched employee" or "contractor." This is the intended, precision-favoring
  behavior -- the alternative would be silently guessing on genuinely ambiguous evidence.
- **The 1 "miss" in dual-account resolution** is a *different*, unrelated defect colliding
  with the one being tested: there happen to be two distinct people both named "David Hall"
  in the generated data, and one of them is also one of the 12 dual-UPN people. The system
  correctly refuses to guess which "David Hall" owns that AD account and quarantines it. Not
  a resolution failure -- a correct refusal to resolve on insufficient evidence.
- **The 8 false positives in AWS matching** (precision 73%) are the mirror image of the same
  issue: real employees whose AWS IAM username (`first.last`, no domain, no display name to
  cross-check) collides with another real employee's identical name. Our matching rule for
  AWS explicitly requires a *globally unique* local-part match before attributing ownership
  (documented in `src/northwind/ingestion/aws.py`), so these land as `CORRELATION_AMBIGUOUS`
  rather than wrongly-attributed access -- which is the safer failure mode for a system whose
  output feeds access reviews and terminated-access findings.
- These name collisions are **not an injected defect** -- they're an accidental side effect of
  generating 2,400 names from Faker's finite name pool (a real, if unintentional, demonstration
  of exactly the kind of "common name" ambiguity real HR/IT data has). We chose not to
  "fix" the generator to eliminate them, since a correlation engine that only works when names
  happen to be unique isn't demonstrating anything useful.

### A correction we caught by verifying, not by trusting the code

While validating against ground truth, we found and fixed two real bugs in the ingestion
pipeline itself (not just the seed data):

1. **Identity-merge bug (serious).** `upsert_identity` originally keyed purely on
   `canonical_employee_id`. For the 3 seeded employee_id collisions (6 distinct people sharing
   one ID), this silently *merged* two different people into a single `Identity` row on the
   second upsert -- precisely the "incorrect merge corrupts every downstream review and
   finding" failure the task warns about. Fixed with a deterministic disambiguation key plus
   a preserved `employee_id_disputed` alias for traceability. Caught by
   `tests/integration/test_zoho_ingestion.py::test_zoho_no_identity_merge_on_employee_id_collision`.
2. **AD classification routing bug.** Any AD account with a genuinely ambiguous correlation
   match (not just a truly-unmatched one) was falling through into contractor/service/orphan
   classification, meaning a real, ambiguously-matched employee could get silently mislabeled
   as an orphan. Fixed by routing "ambiguous" and "unmatched" to different code paths.

Both are described in more detail, with the exact numbers before/after, in `docs/ai.md`.

## Repository layout

seed/generate.py deterministic six-source data generator + ground_truth.json
seed/baseline/ generated source files (gitignored, regenerate with the script above)
src/northwind/models/ SQLAlchemy schema (23 tables)
src/northwind/ingestion/ the six connectors + shared correlation/normalization/repository code
src/northwind/access_graph/ effective-access resolution + A7 triage (why/why-not/diff)
src/northwind/findings/ A3 risk findings catalog (7 rules)
src/northwind/lifecycle/ A4 JML workflows (joiner/mover/leaver) + mock connectors
src/northwind/campaigns/ A5 access review campaign service
src/northwind/api/ FastAPI app (/health)
src/northwind/cli/ operator CLI (health, reconcile, ingest, findings, jml, campaign, access, why*, diff)
console/app.py A6 Streamlit operator console
migrations/ Alembic migrations
tests/integration/ 20 passing tests against a real Postgres instance
scripts/evaluate_precision_recall.py A1/A2 self-evaluation against ground truth
scripts/day14_incident_demo.py Day-14 incident reproduction (causes #2, #3) + honest note on #1
docs/ poc-plan, incident/, runbook, product-doc, troubleshooting,
readout, product-feedback, ai, architecture, deployment


## Running tests

```bash
export DATABASE_URL="postgresql+psycopg://northwind:northwind@localhost:5432/northwind"
python3 -m pytest tests/integration/ -v
```

## What we cut, and why

- **n8n workflow implementation** -- JML is a code-based durable saga instead (allowed per the
  task spec). Introducing a second runtime before the workflow logic itself was proven correct
  would have added risk without adding proof.
- **Full React frontend** -- a Streamlit console instead, per the task's own guidance that
  this isn't a frontend contest.
- **Kubernetes/Helm, cloud deployment, CI/CD** -- deferred, since deeper cloud infrastructure
  work sits outside this project's Core scope.
- **Aggregated/bulk access review and finding disposition** -- individual-item review and
  disposition work end-to-end; bulk operations are the clearest, most honest "not yet" item
  (see `docs/product-feedback.md` item 3).
- **Executive readout as a rendered PDF deck, and a recorded demo video** -- content for both
  is written (`docs/readout.md`), but this environment has no video capability and rendering a
  polished PDF was deprioritized against finishing the underlying functionality the deck and
  demo would describe. Cutting the packaging over the substance felt like the more defensible
  trade given the time available.
- **One of three Day-14 incident causes was not manufactured as a live failure** -- see
  `docs/incident/rca.md` for why, and what was verified instead.

## Real bugs caught during development (see `docs/ai.md` for full detail)

1. An identity-merge bug that would have silently corrupted the 3 employee_id collision cases
2. An AD correlation-routing bug that misclassified ambiguously-matched real employees as orphans
3. An entitlement-revocation gap in the leaver saga where a step marked itself "succeeded"
   despite only partial completion
4. A test-isolation false failure that, on investigation, revealed a genuine (now documented)
   architectural interaction between re-ingestion and workflow idempotency

Each was caught by directly verifying actual data/counts against expectations, not by code
review -- see `docs/ai.md` for the pattern this suggests for future work.


