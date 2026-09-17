from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
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
from northwind.ingestion.normalize import email_local_part, normalize_employee_id
from northwind.ingestion.repositories import get_or_create_application, upsert_account
from northwind.models.ingestion import IngestionRun


def ingest_entra(session: Session, path: Path) -> IngestionRun:
    raw_bytes = path.read_bytes()
    input_hash = hashlib.sha256(raw_bytes).hexdigest()
    run = start_run(session, "entra_id", str(path), input_hash)
    counter = ReconciliationCounter()

    users = json.loads(raw_bytes)
    counter.rows_in = len(users)

    app = get_or_create_application(session, "Microsoft Entra ID", "entra_id", criticality="high")
    index = CorrelationIndex(session)

    for line_no, user in enumerate(users, start=2):  # line 1 is conceptually the array/header
        record, _ = get_or_create_source_record(session, run, path.name, line_no, user)

        local, domain = email_local_part(user.get("userPrincipalName", ""))
        employee_id = normalize_employee_id(user["employeeId"]) if user.get("employeeId") else None
        last_signin = None
        if user.get("lastSignInDateTime"):
            try:
                last_signin = datetime.fromisoformat(user["lastSignInDateTime"].replace("Z", "+00:00"))
            except ValueError:
                pass

        result = index.resolve(local, domain, display_name=user.get("displayName"), employee_id=employee_id)

        if result.decision == "auto_match":
            upsert_account(
                session, application_id=app.id, native_id=user["id"], username=user["userPrincipalName"],
                account_type="human", enabled=bool(user.get("accountEnabled", True)),
                last_activity_at=last_signin, owner_identity_id=result.best.identity_id,
                ownership_status="owned", source_record_id=record.id,
            )
            mark_normalized(record)
            counter.record("normalized", "AUTO_MATCHED")
        else:
            upsert_account(
                session, application_id=app.id, native_id=user["id"], username=user["userPrincipalName"],
                account_type="human", enabled=bool(user.get("accountEnabled", True)),
                last_activity_at=last_signin, owner_identity_id=None,
                ownership_status="unresolved", source_record_id=record.id,
            )
            reason = "CORRELATION_AMBIGUOUS" if result.decision == "quarantine" else "NO_IDENTITY_MATCH"
            open_quarantine(
                session, record, reason,
                f"Entra user {user['userPrincipalName']} (employeeId={user.get('employeeId')!r}) "
                f"did not confidently correlate to a known identity. Top candidates: "
                f"{[(c.identity_name, c.score, c.rule_code) for c in result.candidates[:3]]}",
                candidate_ids=[c.identity_id for c in result.candidates[:3]],
            )
            counter.record("quarantined", reason)

    counter.flush(session, run)
    return run
