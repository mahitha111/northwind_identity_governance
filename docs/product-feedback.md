# Product Feedback — Three Things a Real Customer Would Hate

Ranked by how fast they'd surface in a real deployment.

## 1. The peer-outlier finding is still noisy

Even after tightening it (min cohort size 8, prevalence ceiling 10%, down from an initial pass
that produced ~400 findings), it still produces ~170 findings against a 2,400-person company.
A real customer would triage the first 20, get a mix of "yeah that's legitimate" and "why did
this flag," and start ignoring the rule within a week -- exactly the false-positive death
spiral the task calls out.

**What I'd do about it:** add a second corroborating signal before firing (e.g., require the
privileged entitlement to also be undocumented in a business-justification field, which
doesn't exist yet) rather than tuning thresholds further, since threshold-tuning alone has
diminishing returns on a fundamentally single-signal rule.

**What I'd tell the customer in the meantime:** "This finding is directional, not a citation.
Use it as a starting list for a conversation with the manager, not as a revoke-on-sight
signal, until we've had a review cycle to calibrate it against your actual peer norms."

## 2. AWS/AD correlation silently degrades when names collide

Our correlation engine correctly refuses to guess when two employees share a name (verified:
this happens more often than expected even at 2,400 people). But the customer-facing symptom
is "why doesn't this account show an owner," and the honest answer ("your two Davids confused
the matching") sounds like a bug even though it's the system doing exactly the right thing.

**What I'd do about it:** surface a specific "name collision, needs manual disambiguation"
category distinct from generic "correlation ambiguous," so the console can say "we found two
possible people, pick one" instead of a vague non-match.

**What I'd tell the customer in the meantime:** "A small number of accounts will show as
unresolved specifically because two of your employees share a name closely enough that we
won't guess which one owns it. This is a short, reviewable list, not a systemic problem."

## 3. There's no way to bulk-accept a class of findings

Every finding disposition is one-at-a-time in this build. A real Priya, staring at 62
missing-manager findings, would want to say "these 12 are all new-hire records still being
backfilled, accept all with one justification" -- and can't, yet.

**What I'd do about it:** this is explicitly the aggregated-review Stretch item we deferred;
it's not just a review-campaign feature, findings need the same bulk treatment.

**What I'd tell the customer in the meantime:** "Individual disposition today, with an audit
trail per decision. Bulk disposition is the very next thing we'd build, and we scoped it out
of this POC specifically so we could get you a working single-item flow to react to first."
