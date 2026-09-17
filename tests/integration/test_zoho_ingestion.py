"""Requires a live Postgres reachable via DATABASE_URL (see conftest for skip behavior)."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from northwind.db import session_scope
from northwind.ingestion.zoho import ingest_zoho
from northwind.models.identity import Identity, IdentityAlias
from northwind.models.ingestion import IngestionRun, QuarantineRecord, SourceRecord

SEED_DIR = Path(__file__).resolve().parents[2] / "seed" / "baseline"
ZOHO_CSV = SEED_DIR / "zoho_people_workers.csv"


@pytest.fixture(autouse=True)
def _require_seed_data():
    if not ZOHO_CSV.exists():
        pytest.skip("seed/baseline/zoho_people_workers.csv not generated -- run seed/generate.py first")


def _counts(session):
    return {
        "identities": session.execute(select(func.count()).select_from(Identity)).scalar(),
        "aliases": session.execute(select(func.count()).select_from(IdentityAlias)).scalar(),
        "source_records": session.execute(select(func.count()).select_from(SourceRecord)).scalar(),
        "quarantine_records": session.execute(select(func.count()).select_from(QuarantineRecord)).scalar(),
    }


def test_zoho_reconciliation_invariant():
    with session_scope() as session:
        run = ingest_zoho(session, ZOHO_CSV)
        assert run.rows_in == run.rows_normalized + run.rows_quarantined
        assert run.rows_in == 2400


def test_zoho_no_identity_merge_on_employee_id_collision():
    """The 3 employee_id collisions (6 rows) must produce 6 distinct identities, not 3."""
    with session_scope() as session:
        ingest_zoho(session, ZOHO_CSV)
        disputed = session.execute(
            select(func.count()).select_from(IdentityAlias).where(
                IdentityAlias.alias_type == "employee_id_disputed"
            )
        ).scalar()
        assert disputed == 3  # one disambiguated identity per collision group


def test_zoho_ingestion_is_idempotent():
    with session_scope() as session:
        ingest_zoho(session, ZOHO_CSV)
        before = _counts(session)

    with session_scope() as session:
        ingest_zoho(session, ZOHO_CSV)
        after = _counts(session)

    assert before == after, f"Re-running ingestion changed business/provenance record counts: {before} -> {after}"


def test_malformed_csv_row_is_quarantined_not_dropped():
    with session_scope() as session:
        run = ingest_zoho(session, ZOHO_CSV)
        malformed = session.execute(
            select(func.count()).select_from(QuarantineRecord).where(
                QuarantineRecord.reason_code == "MALFORMED_CSV_ROW"
            )
        ).scalar()
        assert malformed == 1
