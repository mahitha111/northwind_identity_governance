from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from northwind.ingestion.normalize import email_local_part, normalize_name
from northwind.models.identity import (
    Account,
    AccountEntitlement,
    Application,
    Entitlement,
    EntitlementEdge,
    Identity,
    IdentityAlias,
)


def get_or_create_application(session: Session, name: str, source_system: str, criticality: str = "standard") -> Application:
    existing = session.execute(select(Application).where(Application.name == name)).scalar_one_or_none()
    if existing:
        return existing
    app = Application(name=name, source_system=source_system, criticality=criticality)
    session.add(app)
    session.flush()
    return app


def upsert_identity(
    session: Session,
    *,
    canonical_employee_id: Optional[str],
    name: str,
    email: Optional[str],
    worker_type: str = "employee",
    title: Optional[str] = None,
    department: Optional[str] = None,
    location: Optional[str] = None,
    hire_date: Optional[date] = None,
    termination_date: Optional[date] = None,
    lifecycle_status: str = "active",
    source_status_date_conflict: bool = False,
) -> Identity:
    identity = None
    if canonical_employee_id:
        identity = session.execute(
            select(Identity).where(Identity.canonical_employee_id == canonical_employee_id)
        ).scalar_one_or_none()

    if identity is None:
        identity = Identity(canonical_employee_id=canonical_employee_id, name=name)
        session.add(identity)

    identity.name = name
    identity.email = email
    identity.worker_type = worker_type
    identity.title = title
    identity.department = department
    identity.location = location
    identity.hire_date = hire_date
    identity.termination_date = termination_date
    identity.lifecycle_status = lifecycle_status
    identity.source_status_date_conflict = source_status_date_conflict
    session.flush()
    return identity


def ensure_alias(session: Session, identity_id: str, alias_type: str, normalized_value: str,
                  source_record_id: Optional[str] = None) -> IdentityAlias:
    existing = session.execute(
        select(IdentityAlias).where(
            IdentityAlias.identity_id == identity_id,
            IdentityAlias.alias_type == alias_type,
            IdentityAlias.normalized_value == normalized_value,
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    alias = IdentityAlias(identity_id=identity_id, alias_type=alias_type,
                           normalized_value=normalized_value, source_record_id=source_record_id)
    session.add(alias)
    session.flush()
    return alias


def get_or_create_contractor_identity(session: Session, sam_account_name: str, display_name: str) -> Identity:
    """AD-only contractors have no HRIS record. We still model them as a first-class Identity
    (worker_type='contractor') so their access shows up in the same graph/findings/campaigns
    as everyone else, keyed on their AD sam account name since that's the only stable
    identifier they have."""
    existing = session.execute(
        select(IdentityAlias).where(
            IdentityAlias.alias_type == "ad_sam_account_name",
            IdentityAlias.normalized_value == sam_account_name.lower(),
        )
    ).scalar_one_or_none()
    if existing:
        return session.get(Identity, existing.identity_id)

    identity = Identity(canonical_employee_id=None, name=display_name, worker_type="contractor",
                         lifecycle_status="active")
    session.add(identity)
    session.flush()
    ensure_alias(session, identity.id, "ad_sam_account_name", sam_account_name.lower())
    return identity


def upsert_account(
    session: Session,
    *,
    application_id: str,
    native_id: str,
    username: str,
    account_type: str = "human",
    enabled: bool = True,
    last_activity_at: Optional[datetime] = None,
    owner_identity_id: Optional[str] = None,
    ownership_status: str = "unresolved",
    source_record_id: Optional[str] = None,
) -> Account:
    existing = session.execute(
        select(Account).where(Account.application_id == application_id, Account.native_id == native_id)
    ).scalar_one_or_none()
    if existing is None:
        existing = Account(application_id=application_id, native_id=native_id, username=username)
        session.add(existing)

    existing.username = username
    existing.account_type = account_type
    existing.enabled = enabled
    existing.last_activity_at = last_activity_at
    existing.owner_identity_id = owner_identity_id
    existing.ownership_status = ownership_status
    existing.source_record_id = source_record_id
    session.flush()
    return existing


def get_or_create_entitlement(
    session: Session, *, application_id: str, native_id: str, name: str, type_: str, privileged: bool = False,
) -> Entitlement:
    existing = session.execute(
        select(Entitlement).where(Entitlement.application_id == application_id, Entitlement.native_id == native_id)
    ).scalar_one_or_none()
    if existing:
        return existing
    ent = Entitlement(application_id=application_id, native_id=native_id, name=name, type=type_, privileged=privileged)
    session.add(ent)
    session.flush()
    return ent


def ensure_account_entitlement(session: Session, account_id: str, entitlement_id: str,
                                grant_type: str = "direct", source_record_id: Optional[str] = None) -> AccountEntitlement:
    existing = session.execute(
        select(AccountEntitlement).where(
            AccountEntitlement.account_id == account_id, AccountEntitlement.entitlement_id == entitlement_id
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    link = AccountEntitlement(account_id=account_id, entitlement_id=entitlement_id,
                               grant_type=grant_type, source_record_id=source_record_id)
    session.add(link)
    session.flush()
    return link


def ensure_entitlement_edge(session: Session, parent_id: str, child_id: str, relationship_type: str) -> EntitlementEdge:
    existing = session.execute(
        select(EntitlementEdge).where(
            EntitlementEdge.parent_entitlement_id == parent_id, EntitlementEdge.child_entitlement_id == child_id
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    edge = EntitlementEdge(parent_entitlement_id=parent_id, child_entitlement_id=child_id,
                            relationship_type=relationship_type)
    session.add(edge)
    session.flush()
    return edge
