# [P1] Leaver automation misses terminations when HR status disagrees with termination date

**Type:** Bug (customer-impacting, audit-relevant)
**Priority proposed:** P1 -- defend below against three other P1s
**Reporter:** [FDE name], via Northwind Materials POC, Day 14
**Affected customer:** Northwind Materials (active POC, decision in ~1 week)

## Business context

Northwind is evaluating us against two competitors, specifically to fix an audit finding
about incomplete access review evidence. This defect was surfaced by the customer's own IAM
director during the evaluation, directly touching the exact failure mode (terminated workers
retaining access) that is the stated reason they're buying. A second-order concern: their
internal auditor has already exported evidence built on the buggy behavior and handed it to
an external auditor (see "second-order remediation" note at the end).

## Customer impact

3 identities in the customer's real (seeded/simulated) dataset retained active AWS
administrator access for ~5-8 months past their termination date, because leaver automation
checked HR `status == "Terminated"` instead of the termination date. Confirmed present in the
customer's actual data, not a synthetic edge case.

## Reproduction steps

1. Ingest a Zoho People export containing a worker with `termination_date` set to a past date
   but `status` still `"Active"` (120 such rows exist in the current Northwind seed dataset;
   3 are the specific March cohort referenced in the incident).
2. Observe `Identity.lifecycle_status` for that worker -- **expected:** `"terminated"`.
   Confirm this already passes (see fix, below, already shipped).
3. Trigger leaver processing: `northwind jml leaver "<name>" --trigger repro`.
4. **Prior behavior (before fix):** workflow trigger condition checked raw `status`, so it
   never fired for these rows -- access remained active indefinitely.
5. **Current behavior (after fix):** workflow trigger condition checks `lifecycle_status`
   (date-derived), fires correctly, disables accounts and revokes entitlements.

## Logs / evidence

- `scripts/day14_incident_demo.py` reproduces this live against the running deployment and
  prints the exact identity, dates, and code path.
- `tests/integration/test_leaver_workflow.py` is the permanent regression test.

## Root cause

Original design gated the leaver trigger on `status` field equality rather than on the
authoritative `termination_date`. Status is a secondary, human-maintained field; date is
primary. See `docs/incident/rca.md` cause #2 for full detail.

## Fix implemented

`src/northwind/models/identity.py` computes `lifecycle_status` from `termination_date` during
ingestion (`src/northwind/ingestion/zoho.py`), flagging `source_status_date_conflict` when the
two disagree rather than silently trusting either. `src/northwind/lifecycle/leaver.py` was
updated to trigger on `lifecycle_status`. **Status: already shipped and tested**, not just
proposed -- this ticket exists to formally track it in engineering's backlog per the task's
own process (business context, feasibility, priority) rather than to request the fix.

## My feasibility assessment

Low complexity, already implemented; the remaining engineering work is:
1. Promote the freshness-SLA check (cause #3, same incident) from a manual script assertion
   to a first-class pytest test -- ~1 hour.
2. Add a quarantine-volume alert threshold (cause #1) so a 41-row silent quarantine doesn't
   require a customer to notice missing data before anyone looks -- ~4 hours (needs a
   notification channel decision; Slack webhook is the fastest path given this customer
   already lives in Slack for this engagement).

## Recommended priority: P1, defended

I'd keep this above generic P2 backlog items but *below* any P1 involving active data
corruption or security exposure across multiple customers, for one reason: the fix is
already shipped and verified for this customer specifically. What's left is process hardening
(alerting, test promotion), not an open customer-facing defect. If another P1 is competing for
the same engineer's time and involves an *unfixed*, currently-exploitable gap, that one should
win. I'm flagging P1 here because of who's watching (an active competitive eval with an audit
angle), not because the remaining work is itself P1-complexity.

## Second-order remediation note (evidence already exported)

Dana Whitfield had already exported a Salesforce evidence pack (built before the fix) and
handed it to Northwind's external auditor before this was caught. Recommended notification
order: (1) Priya, immediately, since she's the technical owner and needs to know before
telling Dana what to do; (2) Dana, same day, with the corrected evidence pack and a one-line
explanation of exactly what changed between the two exports; (3) we do **not** contact the
external auditor directly -- that relationship is Northwind's, not ours, and Dana should
control how and when her external auditor is looped in. Marcus is informed (see
communications.md message 3) but not asked to make this call; it's Dana's evidence chain to
manage.
