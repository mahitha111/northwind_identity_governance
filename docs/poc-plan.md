# Northwind Materials — POC Plan

## Fifteen discovery questions (and why each matters)

1. **What does "done" look like to Marcus specifically, in numbers?** (★ top-3 if 5 minutes
   with Mahitha) Mahitha can describe the technical bar; only Marcus's number closes the deal.
   Without this, we optimize for a demo that impresses Mahitha and stalls at the executive gate.
2. **What's the exact scope of the next SOX audit cycle, and when is it?** Determines whether
   "prove this works" or "prove this is already evidence-grade for the auditor" is the real
   success criterion — very different bars.
3. **Who is Sergio Ortiz's manager, and does that person know PlantOps data is in scope?**
   (★ top-3) Sergio has no incentive to cooperate; without air cover from above him, "slow to
   respond" becomes "never responds," and PlantOps data silently rots the whole review.
4. **What happened in the last IGA rollout that made it take 18 months and never finish?**
   Mahitha was burned by this. If we repeat the same failure pattern without knowing what it
   was, we lose on pattern-matching alone, regardless of our actual technical quality.
5. **Which of the three vendors has Marcus already ruled out, if any, and why?** Tells us
   what NOT to lead with — if a competitor lost on "too complex," a complex-looking demo
   loses too, regardless of capability.
6. **Is there a hard budget ceiling, and is it capex or opex?** Changes both the pricing
   conversation and which infra options (cloud vs. on-prem) are even viable to propose.
7. **How many people, realistically, will Mahitha's team dedicate to this POC per week?**
   A one-person team gets a different plan than a five-person team; overcommitting them to a
   staged rollout starves the actual acceptance testing.
8. **What does Northwind currently do about contractors who never appear in HR data?**
   (★ top-3) This is a live, unresolved gap on their side (per the environment brief) that we
   need Mahitha's own answer to before we invent one of our own that doesn't match her reality.
9. **Has Dana (Internal Audit) ever been burned by a vendor's evidence pack before?** Shapes
   how skeptically she'll treat our exports and whether we need extra corroboration built in
   from day one rather than added after a trust problem.
10. **What's the actual cadence and format of the two annual access reviews today?** We need
    the literal spreadsheet template and process, not a paraphrase, to know what "faster than
    this" concretely means to the people who live it.
11. **Which AWS accounts, if any, are explicitly out of bounds for us to touch (even
    read-only)?** Security boundaries discovered mid-POC cost days; discovered up front, they
    cost a sentence in the scope doc.
12. **Does Marcus report the SOX finding's remediation status to the board, and on what
    schedule?** If there's a board date, our POC timeline is not really 3 weeks — it's however
    long is left before that report is due, which could be shorter.
13. **Is there an existing SSO/IdP migration in flight that could change who "Mahitha" even is
    by the time we go live?** Org changes mid-POC (Entra tenant consolidation, etc.) can
    invalidate weeks of correlation work if we don't know they're coming.
14. **What's Mahitha's actual level of trust in her own HR data quality, independent of ours?**
    If she already distrusts Zoho's data, our honest quarantine reporting will land as
    validation, not as a product weakness — worth knowing which conversation we're walking into.
15. **Who else, besides these four names, has influence over the final decision?** Procurement,
    legal, or a security architect who hasn't been introduced yet can derail a deal that looked
    closed to everyone we'd actually met.

## Success criteria (agreed in advance, measurable, tied to what Marcus cares about)

| Criterion | Measure | Tied to |
|---|---|---|
| Audit evidence is defensible | Auditor (Dana, simulated) accepts an exported evidence pack without follow-up questions on provenance | The stated SOX finding |
| Terminated-access exposure closed | 100% of terminated-with-active-access findings from baseline have a disposition (revoked or explicitly accepted with justification) by POC end | Marcus's headcount-neutral risk story |
| Review cycle time | A scoped Salesforce campaign completes reviewer decisions in days, not the 11-week baseline | Mahitha's stated pain |
| No headcount added | Achieved without a net-new FTE on Mahitha's team during the POC | Marcus's explicit "not adding headcount" constraint |
| Incident handled transparently | If something breaks during the POC (it will), Mahitha and Dana can point to a specific, documented explanation, not a vague reassurance | Mahitha's prior 18-month-rollout trauma |

## Staged plan, with entry/exit gates

| Stage | Entry gate | Exit gate | Target date |
|---|---|---|---|
| 0. Discovery | Kickoff scheduled | 15 questions answered, success criteria signed off by Mahitha + Marcus | Day 2 |
| 1. Data foundation | Discovery complete | All 6 sources ingested; reconciliation report reviewed WITH Mahitha, no unexplained quarantine | Day 5 |
| 2. Correlation & findings | Stage 1 exit | Mahitha confirms the top 20 findings look directionally right (not perfect — directionally defensible) | Day 8 |
| 3. Lifecycle & review | Stage 2 exit | One real JML dry-run reviewed with Mahitha; one scoped campaign (Salesforce) launched | Day 12 |
| 4. Incident readiness | — (runs continuously) | Whatever breaks between Day 12-16 gets handled per the incident runbook, visibly | Day 14-16 |
| 5. Executive readout | Stage 3 + 4 exit | 25 minutes with Marcus, ask made | Day 18-20 |

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Sergio never provides a usable PlantOps export | High | Medium | Scope PlantOps as best-effort from day one; if Sergio doesn't respond by Stage 1 exit, we proceed without it and say so explicitly in the readout rather than block the whole POC |
| Mahitha's team can't dedicate review time | Medium | High | Confirm capacity in discovery (Q7); if capacity is thin, narrow campaign scope to the highest-value app (Salesforce) rather than all six sources |
| A real production incident (not our simulated one) happens during the POC window | Low | High | Same incident-response process either way; customer sees us handle a real one exactly like the simulated one, which is actually a credibility opportunity if handled well |
| Northwind's actual data is messier than our assumptions | High | Medium | Budget Stage 1 for genuine surprises, not just execution of a known plan; the reconciliation report is designed to surface exactly this |
| Marcus's 25 minutes get cut short or rescheduled | Medium | High | Prepare a 5-minute version of the readout that still lands the one signing moment, in case the full 25 doesn't happen |

## Explicit out-of-scope items, and what we say when asked

- **Aggregated/bulk certification** (certify a group once, apply to all members). *"We're
  proving reliable per-item review evidence in this POC. Bulk certification is a valuable
  accelerant we'd build into a production rollout, but adding it now would mean less time
  validating the controls tied directly to your audit finding — happy to scope it as an
  immediate post-signing priority."*
- **Kubernetes / production-grade cloud deployment.** *"This POC runs in a fully containerized
  environment that mirrors production architecture; the Kubernetes manifests for your actual
  production rollout are a deployment-phase deliverable, not a proof-of-concept one."*
- **Full non-human-identity lifecycle automation** (service account rotation, credential
  vaulting). *"We're surfacing and flagging unowned non-human identities in this POC. Actually
  automating their lifecycle is a natural phase-two, once ownership itself is established —
  you can't automate what you can't yet attribute."*
- **Real Entra/AD/AWS write access during the POC.** *"We provision against mock connectors
  during the POC specifically so nothing we do here can touch your live environment before
  you've decided to trust it. Production connectors are a go-live step, not a POC one."*
