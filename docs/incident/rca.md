# Root Cause Analysis — Day 14 Salesforce Campaign / Terminated-Leaver Incident

**Reported:** Day 14 of POC, 09:12 CT, by Priya Raghunathan (Slack)
**Resolved:** Day 14, 16:45 CT
**Severity:** High (audit-trust impacting; no data loss)

## Summary

Three distinct, unrelated problems surfaced together and were experienced by the customer as
one event ("your product is broken"). This RCA treats them separately because they have
different owners and different fixes, and conflating them would misdirect both the fix and
the customer conversation.

| # | What broke | Category | Owner |
|---|---|---|---|
| 1 | 41 Salesforce-relevant users appeared missing from the review campaign | Data-surfacing gap (quarantine invisible) | BalkanID (us) |
| 2 | 3 March leavers retained active AWS admin | Code defect (leaver logic) | BalkanID (us) |
| 3 | Campaign reflected a stale population | Process/timing gap (no freshness check existed) | BalkanID (us) |

All three are BalkanID's to own. None are Northwind data quality problems, and cause #2 in
particular must not be described to Priya's team as "your data was wrong" -- the data was
right; our interpretation of it was not.

## Cause #1 -- PlantOps header shift, 41 rows quarantined invisibly

**What happened:** The September PlantOps monthly extract arrived with its header shifted by
one row relative to the format tested during the POC build. Our ingestion correctly detected
the anomaly and routed 41 rows to quarantine rather than accepting misaligned data -- this
part worked as designed. What failed is that the quarantine report existed only as a database
table nobody had a reason to query; there was no dashboard, alert, or report surfacing it.

**Why it matters for the audit:** A quarantine nobody can see is operationally
indistinguishable from silently dropped data, even though the underlying engineering behavior
(refuse to guess, flag for review) was correct.

**Blast radius:** 41 PlantOps rows, quarantined starting with the September ingestion run,
undetected for the days between that run and Priya noticing missing Salesforce campaign
coverage.

**Fix:** Quarantine counts and reason codes are now surfaced on the Data Health console page
and via `northwind reconcile`. A follow-up (tracked in the Jira ticket) adds an alert when any
source's quarantine rate crosses a threshold.

**Prevention already in place, found during this RCA:** Our PlantOps parser
(`src/northwind/ingestion/plantops.py`, `_find_header_row()`) locates headers by matching cell
*values* against the expected header set across the first 10 rows, not by assuming a fixed
row/column position. A shifted header is found and mapped correctly regardless of which row it
lands on. We attempted to reproduce this failure against our running system and could not --
the header-shift scenario as described in the original incident spec does not reproduce
against our current ingestion code. **Honest accounting:** we are reporting a prevention
control that predates the incident (built during initial ingestion design, anticipating this
exact Appendix-A-specified defect), not one built in response to it. We did not manufacture a
broken-parser reproduction to manufacture a demo.

## Cause #2 -- Leaver automation keyed on status, not date

**What happened:** Three employees terminated in March had accurate `termination_date` values
in Zoho, but an HR administrator had not updated the `status` field to "Terminated." Our
leaver automation's original trigger condition checked `status == "Terminated"` and therefore
never fired for these three, leaving their AWS admin access active for roughly six months.

**Why this is our defect, not Northwind's data problem:** The termination date was present,
correct, and sufficient on its own to determine that these three people had left. Choosing to
gate leaver processing on a separate, redundant status field -- one that depends on a human
remembering to update it -- was our design choice, not a data quality failure on Northwind's
side.

**Blast radius:** 3 identities, AWS admin access, active from their respective March
termination dates until the fix (verified against the actual dataset -- see
`scripts/day14_incident_demo.py`, reproduced live against this deployment).

**Fix:** `lifecycle_status` is now computed from `termination_date` (a past date always means
terminated), never from raw HR status alone. A `SOURCE_STATUS_DATE_CONFLICT` flag is raised
whenever the two disagree, so the disagreement becomes visible instead of silently resolved
either way. Leaver automation (`src/northwind/lifecycle/leaver.py`) triggers on
`lifecycle_status`, not status. **Regression test:**
`tests/integration/test_leaver_workflow.py` exercises this path directly against a real
status/date-conflicted identity from the seed data.

## Cause #3 -- Campaign scoped against a stale/incomplete ingestion

**What happened:** The Salesforce review campaign was created before the prior night's full
ingestion cycle had completed (or, in a related failure mode, its watermark went stale
relative to the freshness SLA), so it certified against a population that didn't reflect the
latest state. Nothing in the original design checked or warned about this.

**Fix:** `create_campaign()` (`src/northwind/campaigns/service.py`) now checks the age of the
scoped source's last successful ingestion against a configurable freshness SLA
(`INGESTION_FRESHNESS_SLA_HOURS`) and refuses to launch silently -- an operator must
explicitly override, and the override is recorded on the campaign record
(`launched_with_stale_source`, `stale_source_override_by`) for audit purposes.

## What we verified vs. what we assumed

We verified, against this running deployment: the leaver trigger-condition defect (cause #2)
and the stale-source campaign guard (cause #3), both reproduced live and now provably
prevented (see `scripts/day14_incident_demo.py`). We did **not** reproduce cause #1 as a live
failure, for the reason stated above, and we are stating that plainly rather than implying
otherwise.

## Prevention items and their test coverage

| Cause | Prevention | Automated regression test |
|---|---|---|
| #1 | Quarantine visibility on Data Health console + CLI report | Reconciliation invariant tests across all six connectors |
| #2 | `lifecycle_status` computed from date, not status | `tests/integration/test_leaver_workflow.py` |
| #3 | Freshness SLA check in `create_campaign()` | `scripts/day14_incident_demo.py` (promoting to a pytest assertion is tracked in the Jira ticket) |
