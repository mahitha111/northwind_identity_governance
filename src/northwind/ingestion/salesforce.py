from __future__ import annotations

import csv
import hashlib
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from northwind.ingestion.correlation import CorrelationIndex
from northwind.ingestion.helpers import (
    ReconciliationCounter,
    get_or_create_source_record,
    mark_normalized,
    open_quarantine,
    start_run,
)
from northwind.ingestion.normalize import email_local_part
from northwind.ingestion.repositories import (
    ensure_account_entitlement,
    get_or_create_application,
    get_or_create_entitlement,
    upsert_account,
)
from northwind.models.ingestion import IngestionRun


def ingest_salesforce(session: Session, path: Path) -> IngestionRun:
    raw_bytes = path.read_bytes()
    input_hash = hashlib.sha256(raw_bytes).hexdigest()
    run = start_run(session, "salesforce", str(path), input_hash)
    counter = ReconciliationCounter()

    app = get_or_create_application(session, "Salesforce", "salesforce", criticality="crown_jewel")
    index = CorrelationIndex(session)

    with path.open(encoding="utf-8", newline="") as f:
        reader = list(csv.DictReader(f))
    counter.rows_in = len(reader)

    for line_no, row in enumerate(reader, start=2):
        record, _ = get_or_create_source_record(session, run, path.name, line_no, row)

        local, domain = email_local_part(row["email"])
        result = index.resolve(local, domain, display_name=row["name"])

        owner_identity_id = result.best.identity_id if result.decision == "auto_match" else None
        ownership_status = "owned" if owner_identity_id else "unresolved"

        last_login = None
        if row.get("last_login"):
            try:
                last_login = datetime.fromisoformat(row["last_login"])
            except ValueError:
                pass

        account = upsert_account(
            session, application_id=app.id, native_id=row["username"], username=row["username"],
            account_type="human", enabled=row["is_active"].upper() == "TRUE",
            last_activity_at=last_login, owner_identity_id=owner_identity_id,
            ownership_status=ownership_status, source_record_id=record.id,
        )

        # Entitlements are attached regardless of correlation outcome -- the access itself is
        # real and risk-relevant even if we can't yet say confidently whose access it is.
        profile_ent = get_or_create_entitlement(
            session, application_id=app.id, native_id=f"profile:{row['profile']}",
            name=row["profile"], type_="profile", privileged=row["profile"] in
            ("System Administrator", "Sales Admin"),
        )
        ensure_account_entitlement(session, account.id, profile_ent.id, source_record_id=record.id)
        for ps in [p for p in row["permission_sets"].split("|") if p]:
            ps_ent = get_or_create_entitlement(
                session, application_id=app.id, native_id=f"permset:{ps}", name=ps,
                type_="permission_set", privileged=ps in ("Revenue_Admin", "Data_Export"),
            )
            ensure_account_entitlement(session, account.id, ps_ent.id, source_record_id=record.id)

        mark_normalized(record)
        if result.decision == "auto_match":
            counter.record("normalized", "OK")
        else:
            reason = "CORRELATION_LEGACY_DOMAIN" if domain and domain != "northwindmaterials.com" \
                else ("CORRELATION_AMBIGUOUS" if result.decision == "quarantine" else "NO_IDENTITY_MATCH")
            open_quarantine(
                session, record, reason,
                f"Salesforce user {row['username']} did not confidently correlate to a known "
                f"identity (domain={domain!r}). Top candidates: "
                f"{[(c.identity_name, c.score, c.rule_code) for c in result.candidates[:3]]}",
                candidate_ids=[c.identity_id for c in result.candidates[:3]],
            )
            counter.record("quarantined", reason)

    counter.flush(session, run)
    return run
