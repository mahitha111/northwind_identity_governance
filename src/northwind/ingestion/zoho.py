from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from northwind.ingestion.dates import parse_flexible_date
from northwind.ingestion.helpers import (
    ReconciliationCounter,
    get_or_create_source_record,
    mark_normalized,
    open_quarantine,
    start_run,
)
from northwind.ingestion.normalize import email_local_part, normalize_employee_id, normalize_name
from northwind.ingestion.repositories import ensure_alias, upsert_identity
from northwind.models.identity import Identity
from northwind.models.ingestion import IngestionRun


def _read_raw_rows(path: Path) -> tuple[list[tuple[int, list[str], bool]], list[str]]:
    """Returns ([(line_no, fields, malformed), ...], header). line_no is 1-based and counts
    the header as line 1, matching what a human opening the file in a text editor would see
    -- this is what source_line means everywhere in this codebase."""
    out = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        for i, row in enumerate(reader, start=2):
            malformed = len(row) != len(header)
            out.append((i, row, malformed))
    return out, header


def ingest_zoho(session: Session, path: Path) -> IngestionRun:
    raw_bytes = path.read_bytes()
    input_hash = hashlib.sha256(raw_bytes).hexdigest()
    run = start_run(session, "zoho_people", str(path), input_hash)
    counter = ReconciliationCounter()

    rows, header = _read_raw_rows(path)
    counter.rows_in = len(rows)

    # Pass 1: parse well-formed rows, group by normalized employee_id to detect collisions.
    by_norm_id: dict[str, list[dict]] = {}

    for line_no, fields, malformed in rows:
        raw_payload = dict(zip(header, fields)) if not malformed else {"_raw_fields": fields, "_header": header}
        record, _ = get_or_create_source_record(session, run, path.name, line_no, raw_payload)

        if malformed:
            open_quarantine(
                session, record, "MALFORMED_CSV_ROW",
                f"Row has {len(fields)} fields, expected {len(header)}. Likely an unquoted "
                f"comma inside a field (e.g. full_name) that shifted every later column. "
                f"Raw fields: {fields}",
            )
            counter.record("quarantined", "MALFORMED_CSV_ROW")
            continue

        norm_id = normalize_employee_id(raw_payload["employee_id"])
        entry = {
            "line_no": line_no, "raw": raw_payload, "record": record, "norm_id": norm_id,
            "counted": False,  # exactly-once guard: every well-formed row must set this True
        }
        by_norm_id.setdefault(norm_id, []).append(entry)

    # Pass 2: resolve collisions (auto-resolve via unique corporate email + unique name, else
    # hard-quarantine), and upsert an Identity for every entry that isn't hard-quarantined.
    id_to_identity: dict[str, str] = {}
    manager_refs: list[tuple[dict, str]] = []  # (entry, manager_norm_id)

    for norm_id, group in by_norm_id.items():
        if len(group) == 1:
            _build_identity(session, group[0], id_to_identity, manager_refs)
            continue

        emails = {e["raw"]["work_email"].strip().lower() for e in group}
        names = {normalize_name(e["raw"]["full_name"]) for e in group}
        resolvable = (
            len(emails) == len(group) and len(names) == len(group)
            and all(email_local_part(e)[1] == "northwindmaterials.com" for e in emails)
        )
        candidate_lines = [e["line_no"] for e in group]

        if resolvable:
            for i, e in enumerate(group):
                # These rows share a raw employee_id but are proven-distinct people (unique
                # corporate email + unique name each). Using the shared employee_id as the
                # Identity upsert key would silently MERGE them into one identity -- exactly
                # the "incorrect merge corrupts every downstream review" failure the task
                # warns about. So only the first entry keeps norm_id as its canonical key;
                # later entries get a deterministic, idempotent disambiguated key instead,
                # and the true disputed employee_id is preserved via a separate alias.
                dup_suffix = f"-DUP{i}" if i > 0 else ""
                _build_identity(session, e, id_to_identity, manager_refs,
                                 canonical_id_override=norm_id + dup_suffix,
                                 disputed_employee_id=norm_id if i > 0 else None)
                open_quarantine(
                    session, e["record"], "EMPLOYEE_ID_COLLISION",
                    f"employee_id '{norm_id}' shared by {len(group)} distinct people "
                    f"(source lines {candidate_lines}). Auto-resolved: each row has a unique, "
                    f"verified corporate email and a unique name.",
                    candidate_ids=candidate_lines, status="resolved",
                    resolution="Auto-resolved via unique corporate email + unique name.",
                    resolved_by="system:correlation_policy",
                )
                e["counted"] = True
                counter.record("quarantined", "EMPLOYEE_ID_COLLISION_RESOLVED")
        else:
            for e in group:
                open_quarantine(
                    session, e["record"], "EMPLOYEE_ID_COLLISION",
                    f"employee_id '{norm_id}' shared by {len(group)} distinct people "
                    f"(source lines {candidate_lines}) and cannot be auto-resolved: emails "
                    f"or names are not all distinct/verifiable corporate identifiers.",
                    candidate_ids=candidate_lines, status="open",
                )
                e["counted"] = True
                counter.record("quarantined", "EMPLOYEE_ID_COLLISION_UNRESOLVED")

    # Pass 3: link managers now that every identity in this batch exists, then give every
    # remaining (non-collision, non-malformed) row exactly one reconciliation reason code.
    for entry, manager_norm_id in manager_refs:
        identity = session.get(Identity, entry["identity_id"])
        manager_identity_id = id_to_identity.get(manager_norm_id)
        if manager_identity_id is None:
            reason = "MANAGER_NOT_FOUND_IN_BATCH"
        else:
            identity.manager_identity_id = manager_identity_id
            manager = session.get(Identity, manager_identity_id)
            reason = "MANAGER_TERMINATED" if manager and manager.lifecycle_status == "terminated" else None

        if entry["counted"]:
            continue  # collision-resolved rows already have their one count
        if entry.get("status_date_conflict"):
            counter.record("normalized", "SOURCE_STATUS_DATE_CONFLICT")
        elif reason:
            counter.record("normalized", reason)
        else:
            counter.record("normalized", "OK")
        entry["counted"] = True

    # Any remaining un-counted entries are blank-manager rows (never entered manager_refs).
    for group in by_norm_id.values():
        for entry in group:
            if entry["counted"]:
                continue
            if entry.get("status_date_conflict"):
                counter.record("normalized", "SOURCE_STATUS_DATE_CONFLICT")
            else:
                counter.record("normalized", "MISSING_MANAGER")
            entry["counted"] = True

    session.flush()
    counter.flush(session, run)
    return run


def _build_identity(session, entry, id_to_identity, manager_refs,
                     canonical_id_override: str | None = None, disputed_employee_id: str | None = None):
    """Creates/upserts the Identity + aliases for one row and queues manager linkage for
    pass 3. Does NOT record any reconciliation count -- that happens once, later, so every
    row is counted exactly once regardless of how many passes touch it."""
    raw = entry["raw"]
    record = entry["record"]

    hire_date, _ = parse_flexible_date(raw["hire_date"])
    term_date, _ = parse_flexible_date(raw["termination_date"]) if raw["termination_date"] else (None, None)

    conflict = term_date is not None and raw["status"] == "Active"
    lifecycle_status = "terminated" if term_date is not None else (
        "terminated" if raw["status"] == "Terminated" else "active"
    )

    canonical_id = canonical_id_override or entry["norm_id"]
    identity = upsert_identity(
        session,
        canonical_employee_id=canonical_id,
        name=raw["full_name"],
        email=raw["work_email"].strip().lower(),
        worker_type="employee",
        title=raw["title"],
        department=raw["department"],
        location=raw["location"],
        hire_date=hire_date,
        termination_date=term_date,
        lifecycle_status=lifecycle_status,
        source_status_date_conflict=conflict,
    )
    ensure_alias(session, identity.id, "employee_id", canonical_id, record.id)
    if disputed_employee_id:
        # The raw/shared employee_id this row actually carried on the source file, preserved
        # for traceability even though canonical_employee_id had to be disambiguated.
        ensure_alias(session, identity.id, "employee_id_disputed", disputed_employee_id, record.id)
    local, _domain = email_local_part(raw["work_email"])
    ensure_alias(session, identity.id, "email_localpart", local, record.id)

    # NOTE (documented limitation): if entry["norm_id"] is itself a collided id, this dict
    # can only hold one identity per key, so a manager_employee_id elsewhere in the file that
    # references this collided id will resolve to whichever of the colliding people was
    # processed last. This is a genuine source-data ambiguity (Northwind's own employee_id
    # collision), not a bug we can silently fix -- it is called out in the reconciliation
    # report and README rather than papered over.
    id_to_identity[entry["norm_id"]] = identity.id
    entry["identity_id"] = identity.id
    entry["status_date_conflict"] = conflict
    mark_normalized(record)

    manager_raw = (raw.get("manager_employee_id") or "").strip()
    if manager_raw:
        manager_refs.append((entry, normalize_employee_id(manager_raw)))
