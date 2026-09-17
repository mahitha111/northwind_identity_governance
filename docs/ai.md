# AI Leverage

## Methodology and plan

This entire project was built in an agentic coding session (Claude, via a chat interface with
bash/file tools and a real Postgres instance available). The approach throughout was: write
code, immediately run it against real data, verify the actual output against expectations
(often against `seed/ground_truth.json` or direct DB queries), and fix what verification
showed was wrong before moving to the next piece. Nothing in this repository is "written but
never executed" -- every connector, workflow, and finding rule shown as working in the README
was run against a real PostgreSQL 16 database during development, not just written and
assumed correct.

The plan was sequenced deliberately: deterministic seed data first (everything else depends on
it), then A1 ingestion (the highest-risk, most-defect-dense part of the spec), then A2
correlation, then outward through A3-A7 and finally Part B. This followed the task's own
guidance that a thin, verified Core beats a sprawling unverified one.

## Prompts, skills, and setup

No custom skills, subagents, or MCP servers were used -- this was a single continuous agentic
session using the model's native code-execution tools (bash, file read/write) against a real
database. The most important "prompt" in practice wasn't a prompt at all: it was the
discipline of running pytest and direct SQL queries after every non-trivial change, rather
than trusting generated code to be correct because it looked reasonable.

## Where AI made the work materially faster

- **Boilerplate generation at scale.** The 23-table SQLAlchemy schema, six ingestion
  connectors, and the JML saga pattern all follow repetitive structural patterns (get-or-create
  repositories, reconciliation counters, step-based workflow runs). Writing these by hand would
  have taken meaningfully longer than reviewing and correcting AI-generated versions of them.
- **Deterministic seed data generation.** Writing a generator that injects ~15 distinct,
  precisely-specified defects across six file formats (CSV, JSON, XLSX with merged cells and
  multiple sheets) in one pass would be tedious and error-prone by hand; generating it and
  then mechanically verifying every count against the spec was much faster.
- **Cross-referencing the task spec while coding.** Keeping Appendix A's exact defect counts,
  Appendix B's three incident causes, and the correlation policy table all in working memory
  while writing six different connectors is exactly the kind of consistency-checking task
  suited to a single continuous session rather than context-switching between spec and code.

## Where AI was confidently wrong, how it was caught, and what changed afterward

This is the section we're told matters most, so four real examples, in order of severity.

### 1. Identity-merge bug on employee_id collisions (most serious)

**What happened:** The first version of `upsert_identity` keyed purely on
`canonical_employee_id`. For the three seeded employee_id collisions (six distinct people
sharing one ID each), calling it twice with the same colliding ID silently *merged* the second
person into the first person's `Identity` row instead of creating a second, distinct identity.
The code looked correct -- it compiled, ran without error, and produced a plausible-looking
identity count.

**How it was caught:** Not by reading the code more carefully. By writing an integration test
that explicitly asserted `disputed == 3` (one disambiguation alias per collision group) and
watching it fail, then querying the database directly and finding `Total identities: 2396`
instead of the expected `2399`.

**What changed afterward:** Every subsequent get-or-create repository function was written
with an explicit mental test of "what happens if this natural key isn't actually unique," and
the disambiguation pattern (deterministic suffix + a `*_disputed` alias for traceability) was
applied proactively rather than reactively for the AD dual-account and AWS ambiguous-username
cases later.

### 2. AD correlation routing bug: ambiguous matches misclassified as orphans

**What happened:** The first version of the AD connector treated any non-auto-match
correlation result the same way: fall through to contractor/service/orphan classification.
A real employee whose name happened to collide with another employee's (making correlation
genuinely *ambiguous*, not *absent*) could get classified as an "orphan" -- a materially worse
and more misleading outcome than correctly flagging the ambiguity.

**How it was caught:** By computing the *expected* counts from the seed generator's own
parameters and finding the actual `orphan` count (67) was nearly 6x the intended 12. Direct
inspection of the flagged accounts' source descriptions showed real employee job titles, not
the blank descriptions the 12 genuine seeded orphans have.

**What changed afterward:** Split "ambiguous" (a real identifier match exists but is
contested) from "unmatched" (no identifier evidence exists at all) as genuinely different
code paths with different quarantine reason codes, everywhere in the correlation-consuming
connectors, not just AD.

### 3. Entitlement-revocation gap in the leaver saga

**What happened:** The `revoke_entitlements` step marked itself succeeded even when it only
partially completed its work (skipping accounts whose disable step had failed). On a later
successful retry of the previously-failed disable step, the already-succeeded revoke step was
never re-run, meaning that account's entitlements would never actually be revoked.

**How it was caught:** By deliberately forcing a simulated AD connector failure, letting the
workflow record its partial failure, then clearing the failure and replaying, and directly
counting remaining entitlement rows instead of trusting the run's reported "completed" status.
The count was 2, not 0.

**What changed afterward:** The step is now only marked succeeded when zero accounts remain
deferred; a partial completion stays pending and is retried on the next replay. This is now
covered by a permanent regression test asserting the post-recovery entitlement count, not just
the run status.

### 4. Test-isolation false failure that looked like a real regression

**What happened:** After the fix in #3, a test still failed intermittently with the exact
symptom the fix was supposed to prevent. This looked like the fix hadn't actually worked.

**How it was caught, and what it actually was:** Direct inspection of the WorkflowRun table
showed the test was reusing a literal string trigger_event_id across separate pytest
invocations. A stale, already-succeeded run from an earlier invocation was being found and
reused (correct idempotency behavior), but a re-ingestion between invocations had legitimately
re-established AD group-membership entitlements from source-of-truth data, and the cached,
already-succeeded revoke step correctly declined to re-run.

**What this revealed, and what changed:** This wasn't a code bug at all -- it was an
architectural interaction between "ingestion re-establishes source-of-truth state" and "a
workflow run's idempotency correctly avoids redoing already-succeeded work," a real,
documented limitation (see the RCA and README) rather than something quietly patched over.
The test itself was fixed to use a unique trigger id per invocation, since no real termination
event would ever reuse a literal string across genuinely different events.

## What this means for how we'd use AI going forward

The pattern across all four: AI-generated code was consistently plausible and consistently
needed the same check applied -- run it against real data and verify the specific numbers, not
the shape of the output. None of these four bugs would have been caught by code review alone;
all four were caught by an automated or manual assertion on an actual count. The process
change this argues for, beyond this project: for any code whose job is "don't lose or merge
records," write the counting assertion before trusting the implementation, not after.
