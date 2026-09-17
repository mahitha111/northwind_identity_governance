# Data Health & Reconciliation

Every time Northwind identity data is refreshed -- from Zoho People, Microsoft Entra ID,
Active Directory, Salesforce, AWS, or PlantOps -- the platform tells you exactly what happened
to every single record. Nothing is ever silently dropped, merged, or guessed at.

## What you'll see

After each data refresh, open **Data Health** in the console. You'll see, for each of your six
connected sources:

- **Rows in** -- how many records the source sent us
- **Normalized** -- how many were confidently understood and added to your identity graph
- **Quarantined** -- how many need a human decision before we'll act on them

These three numbers always add up: rows in always equals normalized plus quarantined. If they
don't, that's treated as a system error, not a rounding difference -- we'd rather stop and
flag it than show you a number that doesn't add up.

## Why records get quarantined

A record lands in quarantine when we can't confidently answer "whose access is this?" or "is
this data trustworthy?" without guessing. Common reasons you'll see:

- **Two people share the same employee ID** in the source system -- we won't silently merge
  them into one person or arbitrarily pick one; each is preserved as a distinct record with a
  note on why they needed review.
- **A name doesn't clearly match** anyone in your HR system -- could be a contractor, a
  service account, or genuinely nobody we can identify (an "orphan"). We tell you which we
  think it is and why.
- **A spreadsheet extract looks different than usual** -- a shifted header, an unexpected
  second worksheet, or a format we haven't seen before. Rather than guess at what the columns
  mean, we hold the row for review.

## What to do with quarantined records

Click through from the Data Health page to see the exact reason and the original source row.
Most quarantine reasons resolve themselves on the next clean data refresh (e.g., once a
spreadsheet's header is fixed). Records that need a permanent decision (e.g., "yes, this
really is two different people with the same employee ID") can be marked resolved directly,
and that resolution is preserved even when the same source is re-imported later.

## Why this matters for your audit

Your external auditor's most common objection to identity governance evidence is "how do I
know nothing was left out?" The reconciliation report is a direct, mechanical answer: every
row from every source is accounted for, with a reason if it isn't yet part of your access
picture. You can hand this report to an auditor as-is.

## A note on trust

We'd rather show you 150 quarantined PlantOps rows with a clear reason than quietly guess and
show you a clean-looking number that's wrong. If a quarantine count looks unusually high after
a refresh, that's worth investigating -- but it's a sign the system is doing its job, not a
sign something is broken.
