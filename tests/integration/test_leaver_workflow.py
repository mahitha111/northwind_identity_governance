from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, func

from northwind.config import settings
from northwind.db import session_scope
from northwind.ingestion.ad import ingest_ad
from northwind.ingestion.entra import ingest_entra
from northwind.ingestion.zoho import ingest_zoho
from northwind.lifecycle.leaver import run_leaver
from northwind.models.identity import Account, AccountEntitlement, Identity

SEED_DIR = Path(__file__).resolve().parents[2] / "seed" / "baseline"


@pytest.fixture(autouse=True)
def _require_seed_data_and_reset_failure_rates():
    if not (SEED_DIR / "zoho_people_workers.csv").exists():
        pytest.skip("seed/baseline not generated -- run seed/generate.py first")
    yield
    # settings is a process-wide singleton read once at import time, not re-read from the
    # environment per-call, so tests that simulate an outage must mutate it directly (and
    # restore it) rather than os.environ, which would silently have no effect.
    settings.mock_ad_failure_rate = 0.0
    settings.mock_entra_failure_rate = 0.0


def _ensure_base_data():
    with session_scope() as session:
        ingest_zoho(session, SEED_DIR / "zoho_people_workers.csv")
    with session_scope() as session:
        ingest_entra(session, SEED_DIR / "entra_users.json")
    with session_scope() as session:
        ingest_ad(session, SEED_DIR / "ad_accounts.csv")


def _a_terminated_identity_with_accounts(session):
    identities = session.execute(select(Identity).where(Identity.lifecycle_status == "terminated")).scalars().all()
    for i in identities:
        n = session.execute(select(func.count()).select_from(Account).where(Account.owner_identity_id == i.id)).scalar()
        if n > 0:
            return i.id
    pytest.skip("no terminated identity with accounts found in seed data")


def test_leaver_dry_run_does_not_modify_accounts():
    _ensure_base_data()
    with session_scope() as session:
        identity_id = _a_terminated_identity_with_accounts(session)
        before = [a.enabled for a in
                  session.execute(select(Account).where(Account.owner_identity_id == identity_id)).scalars().all()]
        run_leaver(session, identity_id, f"dry-run-test-{uuid.uuid4()}", mode="dry_run")
        after = [a.enabled for a in
                 session.execute(select(Account).where(Account.owner_identity_id == identity_id)).scalars().all()]
        assert before == after, "dry_run must not modify account state"


def test_leaver_execute_disables_accounts_and_revokes_entitlements():
    _ensure_base_data()
    with session_scope() as session:
        identity_id = _a_terminated_identity_with_accounts(session)
        run = run_leaver(session, identity_id, f"execute-test-{uuid.uuid4()}", mode="execute")
        assert run.status == "completed"
        accounts = session.execute(select(Account).where(Account.owner_identity_id == identity_id)).scalars().all()
        assert all(not a.enabled for a in accounts)
        for a in accounts:
            remaining = session.execute(
                select(func.count()).select_from(AccountEntitlement).where(AccountEntitlement.account_id == a.id)
            ).scalar()
            assert remaining == 0


def test_leaver_replay_is_idempotent():
    _ensure_base_data()
    replay_trigger = f"replay-test-{uuid.uuid4()}"
    with session_scope() as session:
        identity_id = _a_terminated_identity_with_accounts(session)
        run1 = run_leaver(session, identity_id, replay_trigger, mode="execute")
        run1_id = run1.id
    with session_scope() as session:
        run2 = run_leaver(session, identity_id, replay_trigger, mode="execute")
        assert run2.id == run1_id, "same trigger_event_id must reuse the same WorkflowRun"
        assert run2.status == "completed"


def test_leaver_partial_failure_then_recovery_fully_revokes():
    _ensure_base_data()
    partial_fail_trigger = f"partial-fail-regression-{uuid.uuid4()}"
    settings.mock_ad_failure_rate = 1.0
    with session_scope() as session:
        # Need an identity with at least one AD-classified account for this to be meaningful.
        identities = session.execute(select(Identity).where(Identity.lifecycle_status == "terminated")).scalars().all()
        identity_id = None
        for i in identities:
            accts = session.execute(select(Account).where(Account.owner_identity_id == i.id)).scalars().all()
            if any("onmicrosoft.com" not in a.username for a in accts) and len(accts) > 1:
                identity_id = i.id
                break
        if identity_id is None:
            pytest.skip("no suitable multi-account terminated identity found")

        run = run_leaver(session, identity_id, partial_fail_trigger, mode="execute")
        assert run.status == "partially_failed"

    settings.mock_ad_failure_rate = 0.0
    with session_scope() as session:
        run = run_leaver(session, identity_id, partial_fail_trigger, mode="execute")
        assert run.status == "completed"
        accounts = session.execute(select(Account).where(Account.owner_identity_id == identity_id)).scalars().all()
        assert all(not a.enabled for a in accounts)
        for a in accounts:
            remaining = session.execute(
                select(func.count()).select_from(AccountEntitlement).where(AccountEntitlement.account_id == a.id)
            ).scalar()
            assert remaining == 0, (
                "regression: entitlements left un-revoked after a delayed-success retry -- "
                "this is the exact 'accumulated access from an unprocessed step' bug"
            )
