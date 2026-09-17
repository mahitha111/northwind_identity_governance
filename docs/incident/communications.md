# Day-14 Incident — Customer Communications

Context: Priya Raghunathan posted in the shared Slack channel at 09:12 CT: "We pulled up the
Salesforce review campaign this morning and 41 users we know have Salesforce access aren't in
it. We also have three people who left in March showing as active with AWS admin. Dana is
asking whether anything in this tool can be trusted for the audit. I need an answer today."

---

## Message 1 — Acknowledgement (sent 09:41 CT, within the hour, before root cause was known)

**To:** Priya Raghunathan (cc: Dana Whitfield)
**Channel:** Slack, #northwind-balkanid-poc
**From:** [FDE name]

> Priya — got this, thank you for flagging it fast. Two separate things in what you're
> describing, and I want to be straight with you on both rather than guess at causes right
> now.
>
> On the 41 Salesforce users: I'm pulling the ingestion and campaign scoping logs now to see
> whether they were excluded, mis-scoped, or something else. I don't have the answer yet and
> I'm not going to speculate on Slack.
>
> On the three March leavers with active AWS admin: same — looking at it now, no cause
> confirmed yet.
>
> What I can tell you right now: I'm not going to ask you to just trust the numbers while
> this is open. I'll have a real update, not a guess, by 12:00 CT today, and I'll loop Dana
> in directly once I know what happened to the 41 users specifically, since that's the audit
> question.
>
> If it turns out any of this is a genuine tool defect, I'll say so plainly — that's on us to
> fix, not something for your team to work around.

*Why this wording: no cause is claimed, no fix-time promise beyond a status checkpoint, and
it doesn't imply Dana's audit concern is dismissed while quietly hoping the numbers are fine.*

---

## Message 2 — Interim update (sent 11:20 CT, cause identified, fix in progress)

**To:** Priya Raghunathan (cc: Dana Whitfield)
**Channel:** Slack, #northwind-balkanid-poc

> Update as promised, ahead of the 12:00 checkmark since I have real answers now.
>
> **The 41 Salesforce users:** this is on us. The September PlantOps extract had its header
> shifted by one row versus the format we'd tested against, which caused our ingestion to
> quarantine 41 rows rather than reject the whole file outright — the safe behavior, but the
> quarantine report wasn't surfaced anywhere you'd actually see it. That's a real gap: a
> quarantine nobody can see is functionally the same as a silent drop. I've confirmed those
> 41 rows are sitting in quarantine right now, not lost — I can get you the exact list within
> the hour.
>
> **The three March leavers with active AWS admin:** this one is more nuanced, and I want to
> be precise instead of vague. Your HR system correctly has termination dates recorded for
> all three. What happened is a mismatch between the termination *date* and the *status*
> field — the status field wasn't updated to "Terminated" for these three, and our earlier
> leaver logic keyed off status rather than the date. That's our bug, not a data problem on
> your end: the date was right there and correct, we just weren't reading the field that
> actually mattered. It's already fixed and I've verified it against your data — leaver
> processing now keys off the termination date, and it correctly catches these three (and
> would have caught them from day one with today's logic).
>
> I don't yet have full confirmation on why the campaign snapshot itself looked stale rather
> than reflecting last night's ingestion — still checking that third piece before I close
> this out. Will have that within the hour too.
>
> None of this changes what Dana needs: I'll have a corrected evidence view before end of day,
> and I'll tell you exactly what changed and why, not just a new number.

*Why this wording: distinguishes a real code defect (leaver logic) from a data-surfacing gap
(quarantine visibility) without pinning either on Northwind's team, and doesn't over-claim on
the third cause before it's actually confirmed.*

---

## Message 3 — Resolution (sent 16:45 CT)

**To:** Priya Raghunathan (cc: Dana Whitfield, Marcus Bell)
**Channel:** Slack, #northwind-balkanid-poc + follow-up email

> Closing the loop on today, with the full picture now confirmed on all three points:
>
> **1. The 41 Salesforce users (data-surfacing gap, not data loss).** Root cause: a shifted
> header in the September PlantOps extract caused 41 rows to be correctly quarantined but
> invisible to anyone without querying the database directly. Fix shipped: quarantine counts
> and reasons are now surfaced on the Data Health dashboard and in a CLI report
> (`northwind reconcile`), and we're adding an alert when quarantine volume crosses a
> threshold on any source. No data was lost or miscounted — it was sitting in the system the
> whole time, just not visible to you.
>
> **2. The three March leavers with active AWS admin (real defect, now fixed).** Root cause:
> our leaver automation checked the HR *status* field, not the termination *date*. Your data
> was correct throughout — three terminations had dates recorded correctly but the status
> field lagged. We've corrected the logic to key on the effective date (with status
> disagreements now flagged, not silently trusted either way), verified against your actual
> data, and added an automated regression test so this specific failure mode can't reappear
> silently.
>
> **3. The stale campaign snapshot.** Root cause: the Salesforce review campaign was
> scoped before the prior night's ingestion had fully completed, so it certified against a
> population that was already out of date, with nothing in the UI warning that it had
> happened. Fix shipped: campaign creation now checks ingestion freshness against a
> configurable SLA and refuses to launch silently against a stale or in-flight source — an
> operator has to explicitly override it, and that override is logged.
>
> **What this means for the audit:** Dana, the current Salesforce campaign reflects a
> corrected, fresh population as of this afternoon. I'm attaching a corrected evidence export
> and a one-page note distinguishing what was a code defect (leaver logic — ours to own),
> what was Northwind source data behaving exactly as recorded (the status/date mismatch — not
> a data quality problem, just two fields disagreeing, which our system should have — and now
> does — flag explicitly), and what was a process/timing issue (the stale snapshot). Happy to
> walk through any of it live.
>
> Marcus — looping you in only because this is the kind of thing that should reach you
> directly rather than through a summary: nothing here changes the POC timeline, and I'd
> rather you hear the specifics from me than a rounded-off version later.

*Why this wording: each cause gets one sentence distinguishing defect vs. data vs. process,
without using that framing to deflect — "root cause: our leaver automation" states ownership
plainly. Marcus is looped in briefly, not overwhelmed with the technical detail he didn't ask
for.*
