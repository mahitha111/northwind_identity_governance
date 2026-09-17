# Troubleshooting Decision Tree

Start here for "something looks wrong." Follow the branch that matches what you're seeing.

## "A number changed and I don't know why"

1. Run `northwind diff <old-run-id> <new-run-id>` for the affected source (get run ids from
   `northwind health`).
2. Did `rows_in` change? -> The source file itself has a different row count. Check with the
   source system owner (e.g., "did HR add contractors to this month's export?") before
   assuming it's a system issue.
3. Did `rows_quarantined` change but `rows_in` didn't? -> Something about the DATA changed in
   a way that affects correlation confidence (e.g., new name collisions, a domain that wasn't
   seen before). Run `northwind reconcile <new-run-id>` and look at which reason code grew.
4. Neither changed, but a finding or campaign number changed? -> That's downstream of
   ingestion. Check whether a `FindingDisposition` expired, or whether accounts were
   disabled/enabled by a JML run between the two points you're comparing (`northwind jml`
   history, or the JML Runs console page).

## "An identity isn't showing up where I expect"

1. Are they in the system at all? -> `northwind access "<name>"`. If this returns nothing,
   they were quarantined during ingestion, not silently dropped -- search
   `northwind reconcile` output or the Data Health page for their source row.
2. Are they in the system but missing from a specific campaign? ->
   `northwind why-not "<name>" "<campaign>"`. This tells you exactly which of three things is
   true: no account in that application, an account with zero entitlements, or a genuine
   ingestion-timing gap (their access was added after the campaign's watermark).
3. Are they in the system with the WRONG access (too much or too little)? ->
   `northwind why "<name>" "<entitlement>"` shows the exact path (direct grant vs. inherited
   through which group/role chain).

## "A JML action didn't do what I expected"

1. Check the run status first: did it say `completed`, `partially_failed`, or did it not run
   at all (check whether the trigger condition was even met -- e.g., leaver only fires for
   `lifecycle_status == "terminated"`, not for raw HR status)?
2. `partially_failed` -> see Runbook Failure #2. This is not a bug; it's the system correctly
   refusing to pretend a partial success was a full one.
3. Ran but nothing changed -> most likely the identity's computed access was already correct
   (mover diffs to zero when there's nothing to grant or revoke -- that's a successful
   outcome, not a no-op failure).

## "I got a quarantine reason code I don't recognize"

Every reason code is defined in the connector that produces it
(`src/northwind/ingestion/<source>.py`). If you're not comfortable reading source, the Data
Health console page's drill-through shows the plain-English explanation stored alongside every
quarantine record -- that explanation is written for a human, not just for a developer.

## "The system says everything is fine but I don't believe it"

This is the right instinct to have about any governance tool, including this one. Two checks
that don't just trust our own "healthy" status:

1. Pick 3 people you know personally left the company. Run `northwind access "<name>"` for
   each and confirm their accounts show `enabled: false`.
2. Pick one application you know has more users than we're showing. Check
   `northwind reconcile` for that source -- if the "missing" users are in quarantine with a
   clear reason, the system is working as designed (holding uncertain data for review, not
   hiding it).

If either check surfaces something that doesn't match reality and isn't explained by a
quarantine reason, that's a real bug -- open it the same way we'd want a customer to open one:
what you expected, what you saw, and which of the above steps you already tried.
