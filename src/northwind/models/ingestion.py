from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from northwind.models.base import Base, TimestampMixin, new_uuid

SOURCE_SYSTEMS = (
    "zoho_people", "entra_id", "active_directory", "salesforce", "aws_iam", "plantops",
)


class IngestionRun(Base, TimestampMixin):
    """One run of one connector against one input snapshot.

    input_hash lets a re-run of an identical input be detected and treated as a no-op for
    idempotency purposes, while still recording a run row so the audit trail is complete.
    """

    __tablename__ = "ingestion_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source_system: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    input_path: Mapped[str] = mapped_column(String(500), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    # status in {"running", "succeeded", "failed", "partially_failed"}
    rows_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_normalized: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_quarantined: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    source_records: Mapped[list["SourceRecord"]] = relationship(back_populates="run")
    reconciliation_metrics: Mapped[list["ReconciliationMetric"]] = relationship(back_populates="run")

    __table_args__ = (
        Index("ix_ingestion_run_source_started", "source_system", "started_at"),
    )


class SourceRecord(Base, TimestampMixin):
    """Every row/record ever read from a source file, verbatim, before any normalization.

    This is the traceability backbone: every normalized record and every quarantined record
    must point back to exactly one of these, which points back to a file and line number.
    """

    __tablename__ = "source_record"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("ingestion_run.id"), nullable=False, index=True)
    source_system: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source_file: Mapped[str] = mapped_column(String(500), nullable=False)
    source_sheet: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    source_line: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # outcome in {"pending", "normalized", "quarantined"}

    run: Mapped["IngestionRun"] = relationship(back_populates="source_records")

    __table_args__ = (
        UniqueConstraint("source_system", "source_file", "source_sheet", "source_line",
                          "raw_hash", name="uq_source_record_identity"),
        Index("ix_source_record_file_line", "source_file", "source_line"),
    )


class QuarantineRecord(Base, TimestampMixin):
    """A row that could not be confidently resolved. Never silently dropped."""

    __tablename__ = "quarantine_record"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source_record_id: Mapped[str] = mapped_column(ForeignKey("source_record.id"), nullable=False, index=True)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    # status in {"open", "resolved", "accepted_as_unresolvable"}
    resolution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    source_record: Mapped["SourceRecord"] = relationship()


class ReconciliationMetric(Base, TimestampMixin):
    """Per-run, per-reason-code row counts. rows_in must always equal the sum of these
    plus normalized count, for every run, for every source -- this is the invariant that
    Gate 1 in the delivery plan checks mechanically, not just by eyeballing a report."""

    __tablename__ = "reconciliation_metric"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("ingestion_run.id"), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    # stage in {"normalized", "quarantined"}
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)

    run: Mapped["IngestionRun"] = relationship(back_populates="reconciliation_metrics")
