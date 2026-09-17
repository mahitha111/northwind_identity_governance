from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from northwind.models.base import Base, TimestampMixin, new_uuid


class Identity(Base, TimestampMixin):
    """A single human (or, rarely, a tracked non-human) resolved from one or more source
    accounts. canonical_employee_id is the cleaned, deduplicated Zoho employee_id where one
    exists; it is nullable for AD-only contractors who have no HRIS record at all."""

    __tablename__ = "identity"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    canonical_employee_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(300), nullable=True, index=True)
    worker_type: Mapped[str] = mapped_column(String(30), nullable=False, default="employee")
    # worker_type in {"employee", "contractor", "unknown"}
    title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    department: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    manager_identity_id: Mapped[Optional[str]] = mapped_column(ForeignKey("identity.id"), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    hire_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    termination_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    lifecycle_status: Mapped[str] = mapped_column(String(30), nullable=False, default="active", index=True)
    # lifecycle_status in {"active", "terminated"} -- computed from status AND effective dates,
    # never from status alone (this is the exact rule the Day-14 incident breaks).
    source_status_date_conflict: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    manager: Mapped[Optional["Identity"]] = relationship(remote_side=[id])
    aliases: Mapped[list["IdentityAlias"]] = relationship(back_populates="identity")
    accounts: Mapped[list["Account"]] = relationship(back_populates="owner_identity")

    __table_args__ = (
        Index("ix_identity_lifecycle_status", "lifecycle_status"),
    )


class IdentityAlias(Base, TimestampMixin):
    """Every alternate name/id form an identity was seen under across sources -- the raw
    material for correlation, and the evidence a `why-not` explanation cites."""

    __tablename__ = "identity_alias"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    identity_id: Mapped[str] = mapped_column(ForeignKey("identity.id"), nullable=False, index=True)
    alias_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # alias_type in {"employee_id", "email", "upn", "name", "sam_account_name"}
    normalized_value: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    source_record_id: Mapped[Optional[str]] = mapped_column(ForeignKey("source_record.id"), nullable=True)

    identity: Mapped["Identity"] = relationship(back_populates="aliases")


class Application(Base, TimestampMixin):
    __tablename__ = "application"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    criticality: Mapped[str] = mapped_column(String(20), nullable=False, default="standard")
    # criticality in {"low", "standard", "high", "crown_jewel"}
    source_system: Mapped[str] = mapped_column(String(50), nullable=False)


class Account(Base, TimestampMixin):
    """A login/identity record in one source system: an AD account, an Entra user, an IAM
    user, a Salesforce user, etc. May or may not be linked to a resolved Identity."""

    __tablename__ = "account"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    application_id: Mapped[str] = mapped_column(ForeignKey("application.id"), nullable=False, index=True)
    native_id: Mapped[str] = mapped_column(String(300), nullable=False)
    username: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    account_type: Mapped[str] = mapped_column(String(30), nullable=False, default="human")
    # account_type in {"human", "service", "shared", "unknown"}
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    owner_identity_id: Mapped[Optional[str]] = mapped_column(ForeignKey("identity.id"), nullable=True, index=True)
    ownership_status: Mapped[str] = mapped_column(String(30), nullable=False, default="unresolved")
    # ownership_status in {"owned", "contractor", "service_owned", "orphan", "unresolved"}
    source_record_id: Mapped[Optional[str]] = mapped_column(ForeignKey("source_record.id"), nullable=True)

    application: Mapped["Application"] = relationship()
    owner_identity: Mapped[Optional["Identity"]] = relationship(back_populates="accounts")
    entitlements: Mapped[list["AccountEntitlement"]] = relationship(back_populates="account")

    __table_args__ = (
        Index("ix_account_app_native", "application_id", "native_id", unique=True),
    )


class CorrelationDecision(Base, TimestampMixin):
    """The evidence and scoring behind every account -> identity link (or non-link).
    This is what a `why`/`why-not` command reads to explain itself."""

    __tablename__ = "correlation_decision"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("account.id"), nullable=False, index=True)
    identity_id: Mapped[Optional[str]] = mapped_column(ForeignKey("identity.id"), nullable=True, index=True)
    decision: Mapped[str] = mapped_column(String(30), nullable=False)
    # decision in {"auto_match", "quarantined", "contractor", "service", "orphan"}
    confidence: Mapped[int] = mapped_column(nullable=False)
    rule_code: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False)
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ingestion_run.id"), nullable=True)


class Entitlement(Base, TimestampMixin):
    __tablename__ = "entitlement"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    application_id: Mapped[str] = mapped_column(ForeignKey("application.id"), nullable=False, index=True)
    native_id: Mapped[str] = mapped_column(String(300), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    # type in {"group", "role", "permission_set", "profile", "policy"}
    privileged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ent_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    application: Mapped["Application"] = relationship()

    __table_args__ = (
        Index("ix_entitlement_app_native", "application_id", "native_id", unique=True),
    )


class AccountEntitlement(Base, TimestampMixin):
    __tablename__ = "account_entitlement"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("account.id"), nullable=False, index=True)
    entitlement_id: Mapped[str] = mapped_column(ForeignKey("entitlement.id"), nullable=False, index=True)
    grant_type: Mapped[str] = mapped_column(String(20), nullable=False, default="direct")
    # grant_type in {"direct", "inherited"}
    granted_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    source_record_id: Mapped[Optional[str]] = mapped_column(ForeignKey("source_record.id"), nullable=True)

    account: Mapped["Account"] = relationship(back_populates="entitlements")
    entitlement: Mapped["Entitlement"] = relationship()

    __table_args__ = (
        Index("ix_account_entitlement_unique", "account_id", "entitlement_id", unique=True),
    )


class EntitlementEdge(Base, TimestampMixin):
    """Nesting/inheritance edges: AD group nesting, Entra group->app role, AWS assume-role
    chains. access_graph resolves effective access by walking these with a depth/cycle guard."""

    __tablename__ = "entitlement_edge"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    parent_entitlement_id: Mapped[str] = mapped_column(ForeignKey("entitlement.id"), nullable=False, index=True)
    child_entitlement_id: Mapped[str] = mapped_column(ForeignKey("entitlement.id"), nullable=False, index=True)
    relationship_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # relationship_type in {"nested_group", "app_role_assignment", "assume_role"}
    source_record_id: Mapped[Optional[str]] = mapped_column(ForeignKey("source_record.id"), nullable=True)

    __table_args__ = (
        Index("ix_entitlement_edge_unique", "parent_entitlement_id", "child_entitlement_id", unique=True),
    )


class AccountRelationship(Base, TimestampMixin):
    """Non-entitlement relationships between accounts -- e.g. 'this service account is
    owned-by this human account', used for the unowned-non-human-identity finding."""

    __tablename__ = "account_relationship"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source_account_id: Mapped[str] = mapped_column(ForeignKey("account.id"), nullable=False, index=True)
    target_account_id: Mapped[str] = mapped_column(ForeignKey("account.id"), nullable=False, index=True)
    relationship_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # relationship_type in {"owns", "depends_on"}
    source_record_id: Mapped[Optional[str]] = mapped_column(ForeignKey("source_record.id"), nullable=True)
