from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from northwind.models.base import Base, TimestampMixin, new_uuid


class Finding(Base, TimestampMixin):
    """One risk finding against one subject (an identity or an account). formula_inputs
    stores the named terms of the risk-score arithmetic so a customer question ("why is
    this a 9 and not a 4") is answered by reading a row, not by re-deriving the model."""

    __tablename__ = "finding"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    rule_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # rule_code in {"TERMINATED_WITH_ACTIVE_ACCESS", "ORPHANED_ACCOUNT", "DORMANT_ACCOUNT",
    #   "UNOWNED_NON_HUMAN_IDENTITY", "PRIVILEGED_PEER_OUTLIER", "SOD_CONFLICT", "MISSING_MANAGER"}
    subject_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # subject_type in {"identity", "account"}
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    # severity in {"low", "medium", "high", "critical"}
    risk_score: Mapped[int] = mapped_column(nullable=False)
    formula_inputs: Mapped[dict] = mapped_column(JSON, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", index=True)
    # status in {"open", "accepted", "suppressed", "resolved"}
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ingestion_run.id"), nullable=True)

    dispositions: Mapped[list["FindingDisposition"]] = relationship(back_populates="finding")

    __table_args__ = (
        Index("ix_finding_rule_status", "rule_code", "status"),
    )


class FindingDisposition(Base, TimestampMixin):
    """An admin's accept/suppress decision on a finding, keyed so it survives re-ingestion.
    finding_key is a stable business key (rule_code + subject) rather than the finding row id,
    since a fresh ingestion generates a fresh finding id for the same underlying condition."""

    __tablename__ = "finding_disposition"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    finding_id: Mapped[str] = mapped_column(ForeignKey("finding.id"), nullable=False, index=True)
    finding_key: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    # action in {"accept", "suppress"}
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)

    finding: Mapped["Finding"] = relationship(back_populates="dispositions")


class WorkflowRun(Base, TimestampMixin):
    """A joiner/mover/leaver execution. The durable saga: step-level state means a run can
    be PARTIALLY_FAILED (e.g. Entra done, AD timed out) without ever silently reporting done."""

    __tablename__ = "workflow_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workflow_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # workflow_type in {"joiner", "mover", "leaver"}
    subject_identity_id: Mapped[str] = mapped_column(ForeignKey("identity.id"), nullable=False, index=True)
    trigger_event_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="execute")
    # mode in {"dry_run", "execute"}
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    # status in {"pending", "running", "completed", "partially_failed", "failed"}

    steps: Mapped[list["WorkflowStep"]] = relationship(back_populates="run", order_by="WorkflowStep.attempt")


class WorkflowStep(Base, TimestampMixin):
    __tablename__ = "workflow_step"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_run.id"), nullable=False, index=True)
    step_name: Mapped[str] = mapped_column(String(100), nullable=False)
    attempt: Mapped[int] = mapped_column(nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # status in {"pending", "succeeded", "failed", "compensated", "skipped"}
    request: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    response: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    compensation_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    run: Mapped["WorkflowRun"] = relationship(back_populates="steps")


class AccessRequest(Base, TimestampMixin):
    __tablename__ = "access_request"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    identity_id: Mapped[str] = mapped_column(ForeignKey("identity.id"), nullable=False, index=True)
    entitlement_id: Mapped[str] = mapped_column(ForeignKey("entitlement.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    # action in {"grant", "revoke"}
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    policy: Mapped[str] = mapped_column(String(100), nullable=False)
    approver_id: Mapped[Optional[str]] = mapped_column(ForeignKey("identity.id"), nullable=True)
    decision: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # decision in {"pending", "auto_approved", "approved", "rejected"}
    workflow_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("workflow_run.id"), nullable=True)


class AuditEvent(Base, TimestampMixin):
    """Append-only. Never updated or deleted at the application layer -- enforced by simply
    not exposing update/delete operations on this model anywhere in the codebase."""

    __tablename__ = "audit_event"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    timestamp: Mapped[datetime] = mapped_column(nullable=False, index=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # actor_type in {"system", "admin", "reviewer", "webhook"}
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    object_type: Mapped[str] = mapped_column(String(50), nullable=False)
    object_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    before_state: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    after_state: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)


class Campaign(Base, TimestampMixin):
    __tablename__ = "campaign"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    scope: Mapped[dict] = mapped_column(JSON, nullable=False)
    ingestion_watermark: Mapped[str] = mapped_column(ForeignKey("ingestion_run.id"), nullable=False)
    due_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", index=True)
    # status in {"draft", "active", "closed"}
    launched_with_stale_source: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stale_source_override_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    review_items: Mapped[list["ReviewItem"]] = relationship(back_populates="campaign")


class ReviewItem(Base, TimestampMixin):
    __tablename__ = "review_item"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaign.id"), nullable=False, index=True)
    identity_id: Mapped[str] = mapped_column(ForeignKey("identity.id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("account.id"), nullable=False, index=True)
    entitlement_id: Mapped[str] = mapped_column(ForeignKey("entitlement.id"), nullable=False, index=True)
    reviewer_id: Mapped[Optional[str]] = mapped_column(ForeignKey("identity.id"), nullable=True, index=True)
    reviewer_route: Mapped[str] = mapped_column(String(50), nullable=False)
    # reviewer_route in {"direct_manager", "nearest_active_manager", "iam_fallback_queue"}
    decision: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # decision in {"approve", "revoke", "delegate"}
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    campaign: Mapped["Campaign"] = relationship(back_populates="review_items")


class Reminder(Base, TimestampMixin):
    __tablename__ = "reminder"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    review_item_id: Mapped[str] = mapped_column(ForeignKey("review_item.id"), nullable=False, index=True)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("identity.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(nullable=False, default=1)
    sent_at: Mapped[datetime] = mapped_column(nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False, default="sent")
    # outcome in {"sent", "acknowledged", "no_response"}


class RevocationTask(Base, TimestampMixin):
    __tablename__ = "revocation_task"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    review_item_id: Mapped[str] = mapped_column(ForeignKey("review_item.id"), nullable=False, index=True)
    workflow_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("workflow_run.id"), nullable=True)
    execution_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # execution_status in {"pending", "executed", "failed"}
    proof: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
