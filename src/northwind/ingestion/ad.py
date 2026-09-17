from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from northwind.ingestion.correlation import CorrelationIndex
from northwind.ingestion.dates import parse_windows_filetime
from northwind.ingestion.helpers import (
    ReconciliationCounter,
    get_or_create_source_record,
    mark_normalized,
    open_quarantine,
    start_run,
)
from northwind.ingestion.normalize import email_local_part
from northwind.ingestion.repositories import (
    get_or_create_application,
    get_or_create_contractor_identity,
    get_or_create_entitlement,
    ensure_account_entitlement,
    ensure_entitlement_edge,
    upsert_account,
)
from northwind.models.identity import Identity
from northwind.models.ingestion import IngestionRun

SERVICE_NAME_PREFIXES = ("svc-", "plant-shared-")
SERVICE_DESCRIPTION_KEYWORDS = ("service", "ci", "automation", "integration")

# The three-level nesting the task spec calls out explicitly. Modeled once, globally, rather
# than inferred per-account, since AD's memberOf field only lists an account's direct groups
# -- the parent chain is directory structure, not per-account data.
KNOWN_GROUP_NESTING = [
    ("Plant-Ops-Leads", "Manufacturing-All"),
    ("Manufacturing-All", "All-Employees"),
]


def _ensure_group_nesting(session: Session, app_id: str):
    for child_name, parent_name in KNOWN_GROUP_NESTING:
        child = get_or_create_entitlement(session, application_id=app_id, native_id=child_name,
                                           name=child_name, type_="group")
        parent = get_or_create_entitlement(session, application_id=app_id, native_id=parent_name,
                                            name=parent_name, type_="group")
        ensure_entitlement_edge(session, parent_id=parent.id, child_id=child.id, relationship_type="nested_group")


def _classify_unmatched(description: str, dn: str, sam: str) -> tuple[str, str]:
    """Returns (classification, rule_reason) for an AD account with no identity match.
    classification in {'contractor', 'service', 'orphan'}."""
    desc_lower = (description or "").lower()
    dn_lower = (dn or "").lower()
    sam_lower = (sam or "").lower()

    if "contractor" in desc_lower or "ou=contractors" in dn_lower:
        return "contractor", "description/DN indicates contractor engagement, human-style account"
    if sam_lower.startswith(SERVICE_NAME_PREFIXES) or any(k in desc_lower for k in SERVICE_DESCRIPTION_KEYWORDS):
        return "service", "service naming pattern or description indicates non-interactive/CI usage"
    return "orphan", "no HR identity, no contractor evidence, no defensible non-human classification"


def ingest_ad(session: Session, path: Path) -> IngestionRun:
    raw_bytes = path.read_bytes()
    input_hash = hashlib.sha256(raw_bytes).hexdigest()
    run = start_run(session, "active_directory", str(path), input_hash)
    counter = ReconciliationCounter()

    app = get_or_create_application(session, "Active Directory", "active_directory", criticality="high")
    _ensure_group_nesting(session, app.id)
    index = CorrelationIndex(session)

    with path.open(encoding="utf-8", newline="") as f:
        reader = list(csv.DictReader(f))
    counter.rows_in = len(reader)

    for line_no, row in enumerate(reader, start=2):
        record, _ = get_or_create_source_record(session, run, path.name, line_no, row)

        local, domain = email_local_part(row["userPrincipalName"])
        sam = row["samAccountName"]
        last_logon, _err = parse_windows_filetime(row["lastLogonTimestamp"])

        result = index.resolve(local, domain, display_name=row["displayName"])
        owner_identity_id = None
        ownership_status = "unresolved"
        reason = None
        ambiguous_candidates = None

        if result.decision == "auto_match":
            owner_identity_id = result.best.identity_id
            ownership_status = "owned"
            reason = "OK"
        else:
            # Fallback: 12 people hold Entra + AD accounts under DIFFERENT UPNs, so the UPN
            # local part won't match for them. samAccountName follows the same first.last
            # convention as the verified corporate email in this environment, so it's an
            # equally strong single-identifier signal here -- try it before giving up.
            sam_candidates = index.lookup_localpart_only(sam.lower())
            if len(sam_candidates) == 1:
                owner_identity_id = sam_candidates[0]
                ownership_status = "owned"
                reason = "OK_SAM_MATCH"
            elif result.decision == "quarantine" or len(sam_candidates) > 1:
                # A real identifier match exists but is ambiguous (e.g. two employees who
                # happen to share a full name, so their UPN/sam local parts collide too).
                # This is a correlation-confidence problem, NOT "no HR identity" -- routing
                # it into contractor/service/orphan classification would misclassify a real,
                # matched-but-ambiguous employee as an orphan, which is worse than quarantining.
                ambiguous_candidates = result.candidates[:3] or [
                    type("C", (), {"identity_id": c, "identity_name": index.name_of(c), "score": 60,
                                   "rule_code": "SAM_LOCALPART_AMBIGUOUS"})()
                    for c in sam_candidates[:3]
                ]
                ownership_status = "unresolved"
                reason = "CORRELATION_AMBIGUOUS"

        account_type = "human"
        if owner_identity_id is None and reason != "CORRELATION_AMBIGUOUS":
            classification, why = _classify_unmatched(row["description"], row["distinguishedName"], sam)
            if classification == "contractor":
                identity = get_or_create_contractor_identity(session, sam, row["displayName"])
                owner_identity_id = identity.id
                ownership_status = "contractor"
                reason = "CONTRACTOR_CLASSIFIED"
            elif classification == "service":
                account_type = "service"
                # Best-effort ownership: does the description name a known identity (e.g. the
                # integration account owned by a March leaver)? Substring match on normalized
                # full names is crude but sufficient for this environment's naming.
                owner = _find_owner_mentioned_in_text(session, row["description"])
                if owner:
                    owner_identity_id = owner.id
                    ownership_status = "service_owned_by_terminated" if owner.lifecycle_status == "terminated" \
                        else "service_owned"
                    reason = "SERVICE_ACCOUNT_OWNER_RESOLVED"
                else:
                    ownership_status = "unresolved"
                    reason = "SERVICE_ACCOUNT_NO_OWNER_EVIDENCE"
            else:
                ownership_status = "orphan"
                reason = "ORPHANED_AD_ACCOUNT"

        account = upsert_account(
            session, application_id=app.id, native_id=sam, username=row["userPrincipalName"],
            account_type=account_type, enabled=row["enabled"].upper() == "TRUE",
            last_activity_at=last_logon, owner_identity_id=owner_identity_id,
            ownership_status=ownership_status, source_record_id=record.id,
        )

        for group_name in [g for g in row["memberOf"].split(";") if g]:
            ent = get_or_create_entitlement(session, application_id=app.id, native_id=group_name,
                                             name=group_name, type_="group")
            ensure_account_entitlement(session, account.id, ent.id, source_record_id=record.id)

        mark_normalized(record)
        if ownership_status in ("orphan",):
            open_quarantine(
                session, record, "ORPHANED_AD_ACCOUNT",
                f"AD account {sam} has no HR match and no contractor/service evidence "
                f"(description: {row['description']!r}). Needs human ownership review.",
            )
            counter.record("quarantined", "ORPHANED_AD_ACCOUNT")
        elif reason == "CORRELATION_AMBIGUOUS":
            open_quarantine(
                session, record, "CORRELATION_AMBIGUOUS",
                f"AD account {sam} ({row['displayName']}) matches more than one candidate "
                f"identity with similar confidence (likely two employees sharing a full name, "
                f"so their AD identifiers collide too). Candidates: "
                f"{[(c.identity_name, c.score, c.rule_code) for c in ambiguous_candidates]}",
                candidate_ids=[c.identity_id for c in ambiguous_candidates],
            )
            counter.record("quarantined", "CORRELATION_AMBIGUOUS")
        elif ownership_status == "unresolved" and reason == "SERVICE_ACCOUNT_NO_OWNER_EVIDENCE":
            open_quarantine(
                session, record, "UNOWNED_SERVICE_ACCOUNT",
                f"AD service account {sam} classified via naming pattern but no owner could "
                f"be identified from its description. Feeds the unowned-non-human-identity finding.",
            )
            counter.record("quarantined", "UNOWNED_SERVICE_ACCOUNT")
        else:
            counter.record("normalized", reason or "OK")

    counter.flush(session, run)
    return run


def _find_owner_mentioned_in_text(session: Session, text: str) -> Identity | None:
    if not text:
        return None
    from sqlalchemy import select
    candidates = session.execute(select(Identity.id, Identity.name)).all()
    text_lower = text.lower()
    for identity_id, name in candidates:
        if name and name.lower() in text_lower:
            return session.get(Identity, identity_id)
    return None
