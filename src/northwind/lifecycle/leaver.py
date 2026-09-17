from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from northwind.lifecycle.connectors import ConnectorError, call_mock_connector
from northwind.models.governance import AuditEvent, WorkflowRun, WorkflowStep
from northwind.models.identity import Account, AccountEntitlement, Identity


def _get_or_create_run(session: Session, identity_id: str, trigger_event_id: str, mode: str) -> WorkflowRun:
    idempotency_key = f"leaver:{identity_id}:{trigger_event_id}"
    existing = session.execute(
        select(WorkflowRun).where(WorkflowRun.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing:
        return existing
    run = WorkflowRun(workflow_type="leaver", subject_identity_id=identity_id,
                       trigger_event_id=trigger_event_id, idempotency_key=idempotency_key,
                       mode=mode, status="running")
    session.add(run)
    session.flush()
    return run


def _get_or_create_step(session: Session, run_id: str, step_name: str) -> WorkflowStep:
    existing = session.execute(
        select(WorkflowStep).where(WorkflowStep.run_id == run_id, WorkflowStep.step_name == step_name)
    ).scalar_one_or_none()
    if existing and existing.status == "succeeded":
        return existing  # already done -- replay must not redo work or double-fire connectors
    if existing:
        existing.attempt += 1
        return existing
    step = WorkflowStep(run_id=run_id, step_name=step_name, attempt=1, status="pending")
    session.add(step)
    session.flush()
    return step


def _audit(session: Session, actor: str, action: str, object_type: str, object_id: str,
           reason: str, before: dict | None, after: dict | None, correlation_id: str):
    session.add(AuditEvent(
        timestamp=datetime.now(timezone.utc), actor_type="system", actor_id=actor, action=action,
        object_type=object_type, object_id=object_id, reason=reason,
        before_state=before, after_state=after, correlation_id=correlation_id,
    ))


def run_leaver(session: Session, identity_id: str, trigger_event_id: str, mode: str = "execute") -> WorkflowRun:
    """Leaver keys on lifecycle_status (computed from termination_date, never from raw HR
    status alone -- see the ingestion layer). This is the exact fix for the Day-14 incident
    cause #2: three terminations recorded with status still 'Active' must still fire."""
    identity = session.get(Identity, identity_id)
    run = _get_or_create_run(session, identity_id, trigger_event_id, mode)

    if identity.lifecycle_status != "terminated":
        run.status = "completed"
        run.error = "Identity is not terminated (lifecycle_status computed from date, not raw status); no-op."
        session.flush()
        return run

    step = _get_or_create_step(session, run.id, "discover")
    accounts = session.execute(select(Account).where(Account.owner_identity_id == identity_id)).scalars().all()
    step.status, step.response = "succeeded", {"account_count": len(accounts), "usernames": [a.username for a in accounts]}
    session.flush()

    any_failed = False
    for account in accounts:
        connector = "entra" if "onmicrosoft.com" in account.username else "ad"
        step_name = f"disable_{connector}:{account.id}"
        step = _get_or_create_step(session, run.id, step_name)
        if step.status == "succeeded":
            continue

        before_state = {"enabled": account.enabled}
        if mode == "dry_run":
            step.status, step.request = "skipped", {"would_disable": account.username, "connector": connector}
            session.flush()
            continue

        try:
            resp = call_mock_connector(connector, "disable_account", {"username": account.username})
            account.enabled = False
            step.status, step.response = "succeeded", resp
            _audit(session, "system:leaver_workflow", "disable_account", "account", account.id,
                   f"Leaver workflow for {identity.name} (terminated {identity.termination_date})",
                   before_state, {"enabled": False}, run.id)
        except ConnectorError as e:
            step.status, step.error = "failed", str(e)
            any_failed = True
        session.flush()

    # Revoke entitlements for accounts we successfully disabled (or would, in dry-run).
    step = _get_or_create_step(session, run.id, "revoke_entitlements")
    if step.status != "succeeded":
        still_enabled = [a for a in accounts if mode == "execute" and a.enabled]
        revoked = 0
        for account in accounts:
            if account in still_enabled:
                continue  # this account's disable step hasn't succeeded yet -- don't revoke
                          # access out from under an unresolved failure; retry on next replay
            links = session.execute(
                select(AccountEntitlement).where(AccountEntitlement.account_id == account.id)
            ).scalars().all()
            if mode == "execute":
                for link in links:
                    session.delete(link)
                    revoked += 1
            else:
                revoked += len(links)
        step.response = {"entitlements_revoked": revoked, "deferred_accounts": len(still_enabled)}
        if mode == "dry_run":
            step.status = "skipped"
        elif still_enabled:
            # Deliberately NOT marked "succeeded": a step that only partially completed its
            # work must stay retryable, or a later successful disable-retry would leave that
            # account's entitlements un-revoked forever (exactly the "accumulated access from
            # an unprocessed step" failure this saga exists to prevent).
            step.status = "pending"
            any_failed = True
        else:
            step.status = "succeeded"
        session.flush()

    # Service accounts this leaver owned don't get destroyed -- they get flagged for urgent
    # ownership transfer, per the task's explicit instruction not to break things that depend
    # on them.
    step = _get_or_create_step(session, run.id, "flag_service_dependencies")
    if step.status != "succeeded":
        owned_services = session.execute(
            select(Account).where(Account.owner_identity_id == identity_id, Account.account_type == "service")
        ).scalars().all()
        step.status = "succeeded"
        step.response = {"service_accounts_needing_new_owner": [a.username for a in owned_services]}
        if owned_services and mode == "execute":
            _audit(session, "system:leaver_workflow", "flag_ownership_transfer_needed", "identity",
                   identity_id, f"{identity.name} owned {len(owned_services)} service account(s); "
                   f"needs urgent ownership transfer, not deletion.", None,
                   {"accounts": [a.username for a in owned_services]}, run.id)
        session.flush()

    run.status = "partially_failed" if any_failed else "completed"
    session.flush()
    return run
