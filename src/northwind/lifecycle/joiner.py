from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from northwind.lifecycle.connectors import ConnectorError, call_mock_connector
from northwind.models.governance import AccessRequest, AuditEvent, WorkflowRun, WorkflowStep
from northwind.models.identity import Account, AccountEntitlement, Entitlement, Identity

AUTO_APPROVE_PREVALENCE = 0.6  # if 60%+ of the peer cohort holds it, it's birthright, auto-approved


def _get_or_create_run(session: Session, identity_id: str, trigger_event_id: str, mode: str) -> WorkflowRun:
    idempotency_key = f"joiner:{identity_id}:{trigger_event_id}"
    existing = session.execute(
        select(WorkflowRun).where(WorkflowRun.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing:
        return existing
    run = WorkflowRun(workflow_type="joiner", subject_identity_id=identity_id,
                       trigger_event_id=trigger_event_id, idempotency_key=idempotency_key,
                       mode=mode, status="running")
    session.add(run)
    session.flush()
    return run


def _peer_birthright_entitlements(session: Session, identity: Identity) -> list[tuple[Entitlement, float]]:
    """Birthright = held by AUTO_APPROVE_PREVALENCE+ of active peers in the same
    (department, title) cohort. Same peer-analysis approach as the outlier finding, applied
    in the opposite direction: common-in-cohort implies 'give it by default' instead of
    'flag it as rare'."""
    peers = session.execute(
        select(Identity).where(Identity.department == identity.department, Identity.title == identity.title,
                                Identity.lifecycle_status == "active", Identity.id != identity.id)
    ).scalars().all()
    if len(peers) < 3:
        return []  # cohort too small to infer a birthright pattern responsibly

    counts: Counter[str] = Counter()
    for p in peers:
        accounts = session.execute(select(Account).where(Account.owner_identity_id == p.id)).scalars().all()
        ent_ids_held = set()
        for a in accounts:
            links = session.execute(select(AccountEntitlement).where(AccountEntitlement.account_id == a.id)).scalars().all()
            ent_ids_held.update(l.entitlement_id for l in links)
        counts.update(ent_ids_held)

    result = []
    for ent_id, n in counts.items():
        prevalence = n / len(peers)
        if prevalence >= AUTO_APPROVE_PREVALENCE:
            result.append((session.get(Entitlement, ent_id), prevalence))
    return result


def run_joiner(session: Session, identity_id: str, trigger_event_id: str, mode: str = "execute") -> WorkflowRun:
    identity = session.get(Identity, identity_id)
    run = _get_or_create_run(session, identity_id, trigger_event_id, mode)

    step_key = "compute_birthright"
    existing_step = session.execute(
        select(WorkflowStep).where(WorkflowStep.run_id == run.id, WorkflowStep.step_name == step_key)
    ).scalar_one_or_none()
    if existing_step and existing_step.status == "succeeded":
        run.status = "completed"
        session.flush()
        return run

    birthright = _peer_birthright_entitlements(session, identity)
    step = existing_step or WorkflowStep(run_id=run.id, step_name=step_key, attempt=1, status="pending")
    session.add(step)
    step.response = {"proposed_entitlements": [e.name for e, _ in birthright]}

    # Ensure this joiner has at least one account per application implied by their birthright
    # set (mock-provisioned into Entra/AD), then grant the entitlements.
    provisioned_accounts: dict[str, Account] = {}
    for ent, prevalence in birthright:
        auto_approved = prevalence >= AUTO_APPROVE_PREVALENCE
        request = AccessRequest(
            identity_id=identity_id, entitlement_id=ent.id, action="grant",
            rationale=f"Held by {prevalence:.0%} of peers in {identity.title}/{identity.department} "
                      f"cohort -- birthright access.",
            policy="auto_approve_birthright" if auto_approved else "named_approver",
            decision="auto_approved" if auto_approved else "pending",
        )
        session.add(request)
        session.flush()

        if mode == "dry_run":
            continue

        if ent.application_id not in provisioned_accounts:
            app_id = ent.application_id
            existing_acct = session.execute(
                select(Account).where(Account.owner_identity_id == identity_id, Account.application_id == app_id)
            ).scalar_one_or_none()
            if existing_acct is None:
                try:
                    call_mock_connector("entra", "create_account", {"identity": identity.name})
                    existing_acct = Account(
                        application_id=app_id, native_id=f"joiner:{identity_id}:{app_id}",
                        username=identity.email or identity.name, account_type="human", enabled=True,
                        owner_identity_id=identity_id, ownership_status="owned",
                    )
                    session.add(existing_acct)
                    session.flush()
                except ConnectorError:
                    request.decision = "pending"  # provisioning failed -- leave for retry, don't grant
                    continue
            provisioned_accounts[app_id] = existing_acct

        account = provisioned_accounts[ent.application_id]
        link_exists = session.execute(
            select(AccountEntitlement).where(AccountEntitlement.account_id == account.id,
                                              AccountEntitlement.entitlement_id == ent.id)
        ).scalar_one_or_none()
        if not link_exists:
            session.add(AccountEntitlement(account_id=account.id, entitlement_id=ent.id, grant_type="direct"))
            request.workflow_run_id = run.id
            session.add(AuditEvent(
                timestamp=datetime.now(timezone.utc), actor_type="system", actor_id="system:joiner_workflow",
                action="grant_entitlement", object_type="account", object_id=account.id,
                reason=request.rationale, before_state=None, after_state={"entitlement": ent.name},
                correlation_id=run.id,
            ))

    step.status = "skipped" if mode == "dry_run" else "succeeded"
    run.status = "completed"
    session.flush()
    return run
