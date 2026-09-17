from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from northwind.lifecycle.joiner import _peer_birthright_entitlements
from northwind.models.governance import AuditEvent, WorkflowRun, WorkflowStep
from northwind.models.identity import Account, AccountEntitlement, Identity


def _get_or_create_run(session: Session, identity_id: str, trigger_event_id: str, mode: str) -> WorkflowRun:
    idempotency_key = f"mover:{identity_id}:{trigger_event_id}"
    existing = session.execute(
        select(WorkflowRun).where(WorkflowRun.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing:
        return existing
    run = WorkflowRun(workflow_type="mover", subject_identity_id=identity_id,
                       trigger_event_id=trigger_event_id, idempotency_key=idempotency_key,
                       mode=mode, status="running")
    session.add(run)
    session.flush()
    return run


def run_mover(session: Session, identity_id: str, trigger_event_id: str, mode: str = "execute") -> WorkflowRun:
    """Recomputes target birthright access for the identity's CURRENT (post-move) attributes
    and diffs it against what they actually hold. Grants what's missing, revokes what's no
    longer justified -- explicitly, so accumulated access from unprocessed moves (the most
    common finding in this industry, per the task brief) doesn't happen here."""
    identity = session.get(Identity, identity_id)
    run = _get_or_create_run(session, identity_id, trigger_event_id, mode)

    step_key = "compute_diff"
    step = session.execute(
        select(WorkflowStep).where(WorkflowStep.run_id == run.id, WorkflowStep.step_name == step_key)
    ).scalar_one_or_none()
    if step and step.status == "succeeded":
        run.status = "completed"
        session.flush()
        return run
    step = step or WorkflowStep(run_id=run.id, step_name=step_key, attempt=1, status="pending")
    session.add(step)

    target_entitlement_ids = {e.id for e, _ in _peer_birthright_entitlements(session, identity)}

    accounts = session.execute(select(Account).where(Account.owner_identity_id == identity_id)).scalars().all()
    held_links: dict[str, AccountEntitlement] = {}
    for a in accounts:
        for link in session.execute(select(AccountEntitlement).where(AccountEntitlement.account_id == a.id)).scalars().all():
            held_links[link.entitlement_id] = link

    to_grant = target_entitlement_ids - set(held_links.keys())
    to_revoke = set(held_links.keys()) - target_entitlement_ids

    step.response = {"to_grant": len(to_grant), "to_revoke": len(to_revoke)}

    if mode == "dry_run":
        step.status = "skipped"
        run.status = "completed"
        session.flush()
        return run

    default_account = accounts[0] if accounts else None
    for ent_id in to_grant:
        if default_account is None:
            continue
        session.add(AccountEntitlement(account_id=default_account.id, entitlement_id=ent_id, grant_type="direct"))
        session.add(AuditEvent(
            timestamp=datetime.now(timezone.utc), actor_type="system", actor_id="system:mover_workflow",
            action="grant_entitlement", object_type="account", object_id=default_account.id,
            reason=f"Mover: {identity.name} now in {identity.title}/{identity.department}, "
                   f"which grants this by birthright.", before_state=None,
            after_state={"entitlement_id": ent_id}, correlation_id=run.id,
        ))

    for ent_id in to_revoke:
        link = held_links[ent_id]
        session.add(AuditEvent(
            timestamp=datetime.now(timezone.utc), actor_type="system", actor_id="system:mover_workflow",
            action="revoke_entitlement", object_type="account", object_id=link.account_id,
            reason=f"Mover: {identity.name}'s new role no longer justifies this access "
                   f"(old-access-left-behind is the #1 audit finding this prevents).",
            before_state={"entitlement_id": ent_id}, after_state=None, correlation_id=run.id,
        ))
        session.delete(link)

    step.status = "succeeded"
    run.status = "completed"
    session.flush()
    return run
