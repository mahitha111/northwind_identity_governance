from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from northwind.models.ingestion import IngestionRun, QuarantineRecord, ReconciliationMetric, SourceRecord


def sha256_of(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def start_run(session: Session, source_system: str, input_path: str, input_hash: str) -> IngestionRun:
    run = IngestionRun(
        source_system=source_system,
        input_path=input_path,
        input_hash=input_hash,
        started_at=datetime.now(timezone.utc),
        status="running",
    )
    session.add(run)
    session.flush()
    return run


def get_or_create_source_record(
    session: Session,
    run: IngestionRun,
    source_file: str,
    source_line: int,
    raw_payload: dict,
    source_sheet: Optional[str] = None,
) -> tuple[SourceRecord, bool]:
    """Idempotency point for provenance: an unchanged row re-ingested on a later run reuses
    its existing SourceRecord rather than creating a duplicate. Returns (record, is_new)."""
    raw_hash = sha256_of(raw_payload)
    existing = session.execute(
        select(SourceRecord).where(
            SourceRecord.source_system == run.source_system,
            SourceRecord.source_file == source_file,
            SourceRecord.source_sheet == source_sheet,
            SourceRecord.source_line == source_line,
            SourceRecord.raw_hash == raw_hash,
        )
    ).scalar_one_or_none()
    if existing:
        return existing, False

    record = SourceRecord(
        run_id=run.id,
        source_system=run.source_system,
        source_file=source_file,
        source_sheet=source_sheet,
        source_line=source_line,
        raw_payload=raw_payload,
        raw_hash=raw_hash,
        outcome="pending",
    )
    session.add(record)
    session.flush()
    return record, True


class ReconciliationCounter:
    """Accumulates (stage, reason_code) -> count for one run, written out at the end.
    stage is 'normalized' or 'quarantined'. A source_record with an open OR resolved
    QuarantineRecord attached always counts under 'quarantined', regardless of whether a
    business record (Account/Identity) was also created for it -- reconciliation reflects
    whether a human needs to look at the row, not whether the row was usable."""

    def __init__(self):
        self.counts: dict[tuple[str, str], int] = {}
        self.rows_in = 0

    def record(self, stage: str, reason_code: str, n: int = 1):
        key = (stage, reason_code)
        self.counts[key] = self.counts.get(key, 0) + n

    def flush(self, session: Session, run: IngestionRun):
        rows_normalized = sum(c for (stage, _), c in self.counts.items() if stage == "normalized")
        rows_quarantined = sum(c for (stage, _), c in self.counts.items() if stage == "quarantined")
        for (stage, reason_code), count in self.counts.items():
            session.add(ReconciliationMetric(run_id=run.id, stage=stage, reason_code=reason_code, row_count=count))
        run.rows_in = self.rows_in
        run.rows_normalized = rows_normalized
        run.rows_quarantined = rows_quarantined
        run.completed_at = datetime.now(timezone.utc)
        run.status = "succeeded"
        session.flush()
        assert run.rows_in == run.rows_normalized + run.rows_quarantined, (
            f"Reconciliation invariant broken for run {run.id} ({run.source_system}): "
            f"rows_in={run.rows_in} != normalized({rows_normalized}) + quarantined({rows_quarantined})"
        )


def open_quarantine(
    session: Session,
    source_record: SourceRecord,
    reason_code: str,
    explanation: str,
    candidate_ids: Optional[list] = None,
    status: str = "open",
    resolution: Optional[str] = None,
    resolved_by: Optional[str] = None,
) -> QuarantineRecord:
    """Get-or-create by (source_record_id, reason_code): re-ingesting an unchanged row must
    not pile up duplicate quarantine entries for the same underlying issue."""
    source_record.outcome = "quarantined"
    existing = session.execute(
        select(QuarantineRecord).where(
            QuarantineRecord.source_record_id == source_record.id,
            QuarantineRecord.reason_code == reason_code,
        )
    ).scalar_one_or_none()
    if existing:
        existing.explanation = explanation
        existing.candidate_ids = candidate_ids
        # Don't clobber a human's prior resolution with a re-run's default "open" status.
        if existing.status == "open" and status != "open":
            existing.status = status
            existing.resolution = resolution
            existing.resolved_by = resolved_by
            existing.resolved_at = datetime.now(timezone.utc)
        session.flush()
        return existing

    q = QuarantineRecord(
        source_record_id=source_record.id,
        reason_code=reason_code,
        explanation=explanation,
        candidate_ids=candidate_ids,
        status=status,
        resolution=resolution,
        resolved_by=resolved_by,
        resolved_at=datetime.now(timezone.utc) if status != "open" else None,
    )
    session.add(q)
    session.flush()
    return q


def mark_normalized(source_record: SourceRecord):
    source_record.outcome = "normalized"
