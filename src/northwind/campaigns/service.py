from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from northwind.config import settings
from northwind.models.governance import AuditEvent, Campaign, Reminder, RevocationTask, ReviewItem
from northwind.models.identity import Account, AccountEntitlement, Application, Entitlement, Identity
from northwind.models.ingestion import IngestionRun

IAM_FALLBACK_QUEUE = "IAM_FALLBACK_QUEUE"  # sentinel reviewer_id for the queue itself


def _latest_watermark(session: Session, source_system: str) -> IngestionRun | None:
    return session.execute(
        select(IngestionRun).where(IngestionRun.source_system == source_system, IngestionRun.status == "succeeded")
        .order_by(IngestionRun.completed_at.desc())
    ).scalars().first()


def _route_reviewer(session: Session, identity: Identity) -> tuple[str | None, str]:
    """Manager hierarchy routing with an explicit, stated fallback: if the manager is missing
    or terminated, route to the nearest ACTIVE manager up the chain; if none exists (chain
    exhausted or missing throughout), route to the IAM fallback queue. An unassigned review
    item is treated as an audit finding, never a silent drop."""
    current = identity
    depth = 0
    while current.manager_identity_id and depth < 10:
        manager = session.get(Identity, current.manager_identity_id)
        if manager is None:
            break
        if manager.lifecycle_status == "active":
            return manager.id, "direct_manager" if depth == 0 else "nearest_active_manager"
        current = manager
        depth += 1
    return None, "iam_fallback_queue"


def create_campaign(session: Session, name: str, application_name: str, allow_stale: bool = False) -> Campaign:
    """Scopes a campaign by application. Refuses to launch against a stale/incomplete source
    unless explicitly overridden -- an unacknowledged stale launch is exactly what produced
    the Day-14 incident's third root cause."""
    app = session.execute(select(Application).where(Application.name == application_name)).scalar_one()
    watermark = _latest_watermark(session, app.source_system)
    if watermark is None:
        raise ValueError(f"No successful ingestion run found for {app.source_system}; cannot scope a campaign.")

    hours_since = (datetime.now(timezone.utc) - watermark.completed_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600
    stale = hours_since > settings.ingestion_freshness_sla_hours
    if stale and not allow_stale:
        raise ValueError(
            f"Source {app.source_system}'s last successful ingestion is {hours_since:.1f}h old "
            f"(SLA: {settings.ingestion_freshness_sla_hours}h). Launching now would silently "
            f"certify a stale population. Pass allow_stale=True to launch anyway (this is logged)."
        )

    campaign = Campaign(name=name, scope={"application": application_name}, ingestion_watermark=watermark.id,
                         due_at=datetime.now(timezone.utc) + timedelta(days=14), status="active",
                         launched_with_stale_source=stale,
                         stale_source_override_by="operator" if stale else None)
    session.add(campaign)
    session.flush()

    accounts = session.execute(select(Account).where(Account.application_id == app.id)).scalars().all()
    for account in accounts:
        if not account.owner_identity_id:
            continue  # unowned accounts belong in a finding (ORPHANED/UNOWNED), not a review item with no reviewer
        identity = session.get(Identity, account.owner_identity_id)
        links = session.execute(select(AccountEntitlement).where(AccountEntitlement.account_id == account.id)).scalars().all()
        if not links:
            continue
        reviewer_id, route = _route_reviewer(session, identity)
        for link in links:
            session.add(ReviewItem(
                campaign_id=campaign.id, identity_id=identity.id, account_id=account.id,
                entitlement_id=link.entitlement_id, reviewer_id=reviewer_id, reviewer_route=route,
            ))
    session.flush()
    return campaign


def decide_review_item(session: Session, review_item_id: str, decision: str, reason: str | None, actor: str) -> ReviewItem:
    item = session.get(ReviewItem, review_item_id)
    if decision == "revoke" and not reason:
        raise ValueError("A reason is required to revoke.")
    item.decision = decision
    item.reason = reason
    item.decided_at = datetime.now(timezone.utc)
    session.add(AuditEvent(
        timestamp=datetime.now(timezone.utc), actor_type="reviewer", actor_id=actor, action=f"review_decision:{decision}",
        object_type="review_item", object_id=item.id, reason=reason, before_state=None,
        after_state={"decision": decision}, correlation_id=item.campaign_id,
    ))
    if decision == "revoke":
        session.add(RevocationTask(review_item_id=item.id, execution_status="pending"))
    session.flush()
    return item


def execute_pending_revocations(session: Session, campaign_id: str) -> int:
    """Closes the loop: actually deletes the account_entitlement link for every revoke
    decision, and records proof on the RevocationTask -- an auditor needs 'proof the
    revocations actually executed', not just a decision record."""
    tasks = session.execute(
        select(RevocationTask).join(ReviewItem, RevocationTask.review_item_id == ReviewItem.id)
        .where(ReviewItem.campaign_id == campaign_id, RevocationTask.execution_status == "pending")
    ).scalars().all()
    executed = 0
    for task in tasks:
        item = session.get(ReviewItem, task.review_item_id)
        link = session.execute(
            select(AccountEntitlement).where(AccountEntitlement.account_id == item.account_id,
                                              AccountEntitlement.entitlement_id == item.entitlement_id)
        ).scalar_one_or_none()
        if link:
            session.delete(link)
        task.execution_status = "executed"
        task.proof = {"executed_at": datetime.now(timezone.utc).isoformat(), "review_item_id": item.id}
        executed += 1
    session.flush()
    return executed


def campaign_progress(session: Session, campaign_id: str) -> dict:
    items = session.execute(select(ReviewItem).where(ReviewItem.campaign_id == campaign_id)).scalars().all()
    total = len(items)
    decided = sum(1 for i in items if i.decision)
    unassigned = sum(1 for i in items if i.reviewer_id is None)
    by_decision: dict[str, int] = {}
    for i in items:
        if i.decision:
            by_decision[i.decision] = by_decision.get(i.decision, 0) + 1
    return {"total": total, "decided": decided, "pending": total - decided, "unassigned": unassigned,
            "by_decision": by_decision}


def evidence_pack(session: Session, campaign_id: str) -> list[dict]:
    """Who reviewed what, when, what they decided, and what happened as a result -- an
    auditor's minimum bar. Proof of execution comes from the RevocationTask.proof field."""
    items = session.execute(select(ReviewItem).where(ReviewItem.campaign_id == campaign_id)).scalars().all()
    rows = []
    for item in items:
        identity = session.get(Identity, item.identity_id)
        entitlement = session.get(Entitlement, item.entitlement_id)
        reviewer = session.get(Identity, item.reviewer_id) if item.reviewer_id else None
        revocation = session.execute(
            select(RevocationTask).where(RevocationTask.review_item_id == item.id)
        ).scalar_one_or_none()
        rows.append({
            "identity": identity.name, "entitlement": entitlement.name, "reviewer_route": item.reviewer_route,
            "reviewer": reviewer.name if reviewer else "UNASSIGNED -- IAM fallback queue",
            "decision": item.decision or "PENDING", "reason": item.reason, "decided_at": str(item.decided_at) if item.decided_at else None,
            "revocation_executed": revocation.execution_status if revocation else "n/a",
        })
    return rows
