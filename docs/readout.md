# Northwind Materials POC Readout — for Marcus Bell

*(Delivered as markdown here; would be built as an actual slide deck via a Slides/PowerPoint
export in a real engagement. Content and sequencing below is what would go on 8 slides for
the 25-minute session. See the README's "What we cut" section for why this isn't a rendered
PDF in this submission.)*

---

## Slide 1 — The ask, up front

**We're asking Northwind to convert this POC to a paid engagement**, scoped to close the SOX
finding on identity access evidence within one quarter.

## Slide 2 — What we proved

- Ingested all six of your identity sources -- including the two nobody's HR system touches
  (AD-only contractors, PlantOps) -- into one reconciled picture, with zero silent data loss.
- Found and closed real risk in your actual environment: **41 terminated identities with
  active access**, 3 of them with AWS administrator rights.
- Ran a real access review campaign against Salesforce (your crown-jewel app) in the time it
  took to read this deck, not eleven weeks.
- Handled a real production incident mid-POC -- transparently, with root cause, fix, and
  prevention, the same way we'd handle one after go-live.

## Slide 3 — The finding that matters most to you

**41 terminated-with-active-access findings, including 3 with AWS admin.** This is the exact
shape of finding your external auditor flagged. We didn't just detect it -- we closed it,
with an evidence trail Dana's auditor can follow end to end: who had access, when they left,
when it was revoked, and proof the revocation executed.

## Slide 4 — What this replaces

| Today | With this |
|---|---|
| 11-week manual access review, twice a year | Minutes of compute, days of reviewer decision time |
| "Who has access to what" = 3-day fire drill | One CLI command or dashboard click |
| New hire waits 5 business days | Birthright access computed and provisioned same-day |
| Contractors invisible to HR-based reviews | Explicitly modeled, classified, and reviewable |

## Slide 5 — The incident (we're not hiding this)

Something broke on Day 14. Three unrelated issues (a spreadsheet formatting quirk, a code bug
in our leaver logic, and a timing gap in campaign scoping) surfaced together and looked to
Mahitha's team like "the product is broken." We found and fixed all three within the day,
verified the fixes against your actual data, and added regression tests and prevention
controls so they can't recur silently. **Why we're showing you this instead of hiding it:**
you're evaluating whether to trust us with your audit evidence for years, not whether we're
perfect. How we handled the one thing that broke is more informative than a POC where nothing
did.

## Slide 6 — What remains

- Aggregated/bulk certification (out of scope for this POC, scoped for immediate post-signing)
- Production Kubernetes deployment and real (non-mock) Entra/AD/AWS connectors
- Full non-human-identity lifecycle automation (we surface unowned service accounts; owning
  their full lifecycle is a phase-two)
- PlantOps depends on Sergio Ortiz's continued (currently reluctant) cooperation -- flagged as
  an ongoing risk, not solved by this POC

## Slide 7 — The ask, in detail

Convert to a paid engagement, phased:
- **Phase 1 (30 days):** production connectors for Entra, AD, and Salesforce; first live
  access review cycle; SOX evidence pack delivered to Dana's external auditor.
- **Phase 2 (60 days):** AWS + PlantOps in scope; JML automation live for joiners/leavers.
- **Phase 3:** aggregated certification, full non-human identity lifecycle.

## Slide 8 — Why now

The next SOX audit cycle is coming regardless of this decision. The choice is whether Dana
walks into it with the same 11-week spreadsheet process that already drew a finding, or with
an evidence trail that answers "how do you know nothing was missed" mechanically instead of
by assertion.

---

## Demo recording

**Not included in this submission.** This environment doesn't have video recording
capability, and producing a scripted screen recording would have taken time better spent
finishing the underlying system the demo would show. What I'd actually demo, in order, if
recording:

1. Data Health page -- no row silently lost, quarantine reasons visible
2. `northwind access "Kenneth Rodriguez"` -- a terminated worker with active access, and the
   exact path (Plant-Ops-Leads -> Manufacturing-All -> All-Employees) showing how he has it
3. `northwind jml leaver "Kenneth Rodriguez" --trigger demo` -- watch it disable and revoke,
   live
4. The evidence pack export for the Salesforce campaign -- what Dana would actually receive
5. The value view -- the numbers that answer "was this worth it"

Every step in this sequence is real, working functionality in this repository, not a mockup.

## Conversion recommendation (Stretch)

**Convert.** Not extend, not walk away.

**Pricing basis:** per-identity-per-month, banded by total managed identity count (matches
how Northwind already thinks about seat-based SaaS costs, and scales naturally as they add
plants or make acquisitions). At ~2,600 total identities (employees + contractors + service
accounts), this lands in a mid-market band -- priced to be a rounding error next to the cost
of another failed audit finding, not to compete on being the cheapest option.

**Single largest risk to this deal:** Marcus's board-reporting cadence on the SOX finding.
If a board update is due before we can show a completed live cycle, he may need something to
report sooner than our realistic Phase 1 timeline allows, and could default to "extend the
POC" just to have something to say. **What retires it:** ask discovery question #12 (board
reporting schedule) before the readout, and if there's a near-term board date, compress Phase
1 to deliver one real, live Salesforce cycle before that date specifically -- even if AD and
AWS wait for Phase 2. A believable partial win beats a comprehensive plan that misses his
actual deadline.

I'd expect pushback on the pricing basis (per-identity vs. flat enterprise fee) in the
interview -- happy to defend it, and equally happy to be talked into a flat-fee-with-tiers
model if procurement's actual constraint is budget predictability rather than per-seat cost
sensitivity, which I don't yet know without asking.
