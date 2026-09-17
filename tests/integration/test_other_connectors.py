"""Requires a live Postgres reachable via DATABASE_URL. Mirrors test_zoho_ingestion.py's
pattern for the other five connectors: every one must satisfy the same two contracts --
the reconciliation invariant, and idempotency on re-run."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from northwind.db import session_scope
from northwind.ingestion.ad import ingest_ad
from northwind.ingestion.aws import ingest_aws
from northwind.ingestion.entra import ingest_entra
from northwind.ingestion.plantops import ingest_plantops
from northwind.ingestion.salesforce import ingest_salesforce
from northwind.ingestion.zoho import ingest_zoho
from northwind.models.identity import Account, AccountEntitlement, Entitlement
from northwind.models.ingestion import QuarantineRecord, SourceRecord

SEED_DIR = Path(__file__).resolve().parents[2] / "seed" / "baseline"

CONNECTORS = {
    "entra": (ingest_entra, SEED_DIR / "entra_users.json"),
    "ad": (ingest_ad, SEED_DIR / "ad_accounts.csv"),
    "salesforce": (ingest_salesforce, SEED_DIR / "salesforce_users.csv"),
    "aws": (ingest_aws, SEED_DIR / "aws_iam.json"),
    "plantops": (ingest_plantops, SEED_DIR / "plantops_access_202609.xlsx"),
}


@pytest.fixture(autouse=True)
def _require_seed_data():
    if not (SEED_DIR / "zoho_people_workers.csv").exists():
        pytest.skip("seed/baseline not generated -- run seed/generate.py first")


def _seed_zoho_once():
    """Every non-Zoho connector correlates against identities Zoho creates, so it must run
    first in every test in this file."""
    with session_scope() as session:
        ingest_zoho(session, SEED_DIR / "zoho_people_workers.csv")


def _counts(session):
    return {
        "accounts": session.execute(select(func.count()).select_from(Account)).scalar(),
        "entitlements": session.execute(select(func.count()).select_from(Entitlement)).scalar(),
        "account_entitlements": session.execute(select(func.count()).select_from(AccountEntitlement)).scalar(),
        "source_records": session.execute(select(func.count()).select_from(SourceRecord)).scalar(),
        "quarantine_records": session.execute(select(func.count()).select_from(QuarantineRecord)).scalar(),
    }


@pytest.mark.parametrize("name", list(CONNECTORS.keys()))
def test_reconciliation_invariant(name):
    _seed_zoho_once()
    ingest_fn, path = CONNECTORS[name]
    with session_scope() as session:
        run = ingest_fn(session, path)
        assert run.status == "succeeded", f"{name} run did not succeed: {run.error}"
        assert run.rows_in == run.rows_normalized + run.rows_quarantined, (
            f"{name}: rows_in={run.rows_in} != normalized({run.rows_normalized}) "
            f"+ quarantined({run.rows_quarantined})"
        )
        assert run.rows_in > 0


@pytest.mark.parametrize("name", list(CONNECTORS.keys()))
def test_connector_is_idempotent(name):
    _seed_zoho_once()
    ingest_fn, path = CONNECTORS[name]

    with session_scope() as session:
        ingest_fn(session, path)
        before = _counts(session)

    with session_scope() as session:
        ingest_fn(session, path)
        after = _counts(session)

    assert before == after, f"{name}: re-running ingestion changed record counts: {before} -> {after}"


def test_plantops_excludes_stale_worksheet_explicitly():
    _seed_zoho_once()
    with session_scope() as session:
        ingest_plantops(session, CONNECTORS["plantops"][1])
        stale_count = session.execute(
            select(func.count()).select_from(QuarantineRecord).where(
                QuarantineRecord.reason_code == "STALE_WORKSHEET_EXCLUDED"
            )
        ).scalar()
        assert stale_count > 0, "expected the prior-year PlantOps worksheet to be explicitly excluded"


def test_aws_transitive_admin_chain_is_modeled():
    _seed_zoho_once()
    with session_scope() as session:
        ingest_aws(session, CONNECTORS["aws"][1])
        chain_role = session.execute(
            select(Entitlement).where(Entitlement.native_id == "role:role-deploy-automation-000")
        ).scalar_one_or_none()
        assert chain_role is not None, "expected the assume-role chain's entry role to be ingested"
