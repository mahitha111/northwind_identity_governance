from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from northwind.access_graph.effective_access import effective_access_for_identity, find_identity_by_name
from northwind.models.governance import Campaign, ReviewItem
from northwind.models.identity import Account, AccountEntitlement, Application, Identity
from northwind.models.ingestion import IngestionRun


def why_not_in_campaign(session: Session, identity_name: str, campaign_id_or_name: str) -> str:
    """Explains, citing underlying data, why an identity did or did not land in a campaign."""
    identity = find_identity_by_name(session, identity_name)
    if not identity:
        return f"No identity matches '{identity_name}'."

    campaign = session.get(Campaign, campaign_id_or_name)
    if campaign is None:
        campaign = session.execute(
            select(Campaign).where(Campaign.name.ilike(f"%{campaign_id_or_name}%"))
        ).scalars().first()
    if campaign is None:
        return f"No campaign matches '{campaign_id_or_name}'."

    items = session.execute(
        select(ReviewItem).where(ReviewItem.campaign_id == campaign.id, ReviewItem.identity_id == identity.id)
    ).scalars().all()
    if items:
        lines = [f"{identity.name} IS in campaign '{campaign.name}' with {len(items)} review item(s):"]
        for i in items:
            reviewer = session.get(Identity, i.reviewer_id) if i.reviewer_id else None
            lines.append(f"  - entitlement_id={i.entitlement_id}, routed to "
                         f"{reviewer.name if reviewer else 'IAM fallback queue'} ({i.reviewer_route}), "
                         f"decision={i.decision or 'pending'}")
        return "\n".join(lines)

    app_name = campaign.scope.get("application")
    accounts = session.execute(select(Account).where(Account.owner_identity_id == identity.id)).scalars().all()
    app_accounts = [a for a in accounts if session.get(Application, a.application_id).name == app_name]

    if not app_accounts:
        return (f"{identity.name} is NOT in campaign '{campaign.name}' because they have no "
                f"account in {app_name} at all (checked via owner_identity_id on the account table).")

    for a in app_accounts:
        links = session.execute(select(AccountEntitlement).where(AccountEntitlement.account_id == a.id)).scalars().all()
        if not links:
            return (f"{identity.name} is NOT in campaign '{campaign.name}': their {app_name} "
                    f"account ({a.username}) has zero entitlements on record, so there was "
                    f"nothing to scope into a review item.")

    watermark = session.get(IngestionRun, campaign.ingestion_watermark)
    return (f"{identity.name} is NOT in campaign '{campaign.name}' despite having an account "
            f"and entitlements in {app_name}. The campaign was scoped against ingestion run "
            f"{watermark.id[:8]} (completed {watermark.completed_at}); if this identity's "
            f"{app_name} account was added or corrected AFTER that ingestion run, it would "
            f"correctly be excluded -- re-scope a new campaign to pick it up. Check "
            f"`northwind reconcile` for that run to see if this row was quarantined instead "
            f"of normalized.")


def why_has_entitlement(session: Session, identity_name: str, entitlement_name: str) -> str:
    identity = find_identity_by_name(session, identity_name)
    if not identity:
        return f"No identity matches '{identity_name}'."
    grants = effective_access_for_identity(session, identity.id)
    matches = [g for g in grants if entitlement_name.lower() in g.entitlement_name.lower()]
    if not matches:
        return f"{identity.name} does NOT have '{entitlement_name}' (checked direct + inherited access)."
    lines = [f"{identity.name} has '{entitlement_name}':"]
    for g in matches:
        how = "direct grant" if g.grant_type == "direct" else " -> ".join(g.path)
        lines.append(f"  - via {g.application_name}, account {g.account_username}: {how}")
    return "\n".join(lines)


def diff_runs(session: Session, run_a_id: str, run_b_id: str) -> str:
    """What changed between two ingestions, and what that did to reconciliation counts.
    Full downstream finding-diff is a stretch extension; this covers the run-level comparison
    a customer asking 'this number was different yesterday' needs first."""
    run_a = session.get(IngestionRun, run_a_id)
    run_b = session.get(IngestionRun, run_b_id)
    if not run_a or not run_b:
        return "One or both run ids not found."

    lines = [f"Run A ({run_a.source_system}, {run_a.started_at}): "
             f"in={run_a.rows_in} normalized={run_a.rows_normalized} quarantined={run_a.rows_quarantined}",
             f"Run B ({run_b.source_system}, {run_b.started_at}): "
             f"in={run_b.rows_in} normalized={run_b.rows_normalized} quarantined={run_b.rows_quarantined}"]

    if run_a.rows_in != run_b.rows_in:
        lines.append(f"Row count changed: {run_a.rows_in} -> {run_b.rows_in} "
                     f"({run_b.rows_in - run_a.rows_in:+d})")
    if run_a.rows_quarantined != run_b.rows_quarantined:
        lines.append(f"Quarantine count changed: {run_a.rows_quarantined} -> {run_b.rows_quarantined} "
                     f"({run_b.rows_quarantined - run_a.rows_quarantined:+d}) -- run "
                     f"`northwind reconcile` against each run id for the reason-code breakdown.")
    return "\n".join(lines)
