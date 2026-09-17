from __future__ import annotations

import hashlib
from datetime import date, datetime
from pathlib import Path

import openpyxl
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from northwind.ingestion.dates import parse_flexible_date
from northwind.ingestion.helpers import (
    ReconciliationCounter,
    get_or_create_source_record,
    mark_normalized,
    open_quarantine,
    start_run,
)
from northwind.ingestion.normalize import normalize_name
from northwind.ingestion.repositories import (
    ensure_account_entitlement,
    get_or_create_application,
    get_or_create_entitlement,
    upsert_account,
)
from northwind.models.identity import Identity
from northwind.models.ingestion import IngestionRun

EXPECTED_HEADERS = {"employee name", "role", "granted by", "date"}
FUZZY_UNIQUE_THRESHOLD = 90
CONTEXTUAL_DEPARTMENT = "Manufacturing"


def _find_header_row(ws, max_scan_rows: int = 10) -> tuple[int, dict[str, int]] | tuple[None, None]:
    """Scans the first few rows for the header (PlantOps extracts routinely have 1-3 banner
    rows above it, sometimes with merged cells). Returns (row_index, {lower_header: col_idx})
    or (None, None) if no row in range looks like a header at all."""
    for r in range(1, max_scan_rows + 1):
        found = {}
        for c in range(1, 8):
            val = ws.cell(row=r, column=c).value
            if isinstance(val, str) and val.strip().lower() in EXPECTED_HEADERS:
                found[val.strip().lower()] = c
        if len(found) >= 3:
            return r, found
    return None, None


def _sheet_max_date(ws, header_row: int, date_col: int) -> date | None:
    max_d = None
    for r in range(header_row + 1, ws.max_row + 1):
        val = ws.cell(row=r, column=date_col).value
        d = None
        if isinstance(val, datetime):
            d = val.date()
        elif isinstance(val, date):
            d = val
        elif isinstance(val, str):
            d, _ = parse_flexible_date(val)
        if d and (max_d is None or d > max_d):
            max_d = d
    return max_d


def _parse_name_variant(raw: str) -> str:
    """Normalize the three PlantOps name formats into a comparable 'first last' string.
    'Last, First' and 'First L.' both need repair; 'first.last@' is handled by the caller via
    a direct email-local-part lookup, so it never reaches this function in practice, but we
    fall back to a best-effort split here too in case that lookup ever misses."""
    raw = raw.strip()
    if "," in raw:
        last, first = [p.strip() for p in raw.split(",", 1)]
        first = first.replace("Jr.", "").replace("Sr.", "").strip()
        return f"{first} {last}"
    if raw.endswith("@"):
        local = raw[:-1]
        parts = local.split(".")
        return " ".join(p.capitalize() for p in parts)
    return raw  # "First L." style -- handled by prefix-aware fuzzy matching below


def ingest_plantops(session: Session, path: Path) -> IngestionRun:
    raw_bytes = path.read_bytes()
    input_hash = hashlib.sha256(raw_bytes).hexdigest()
    run = start_run(session, "plantops", str(path), input_hash)
    counter = ReconciliationCounter()

    app = get_or_create_application(session, "PlantOps", "plantops", criticality="standard")
    identities = session.execute(select(Identity.id, Identity.name, Identity.department)).all()

    wb = openpyxl.load_workbook(path, data_only=True)
    sheet_info = {}
    for name in wb.sheetnames:
        ws = wb[name]
        header_row, cols = _find_header_row(ws)
        if header_row is None:
            continue
        max_date = _sheet_max_date(ws, header_row, cols.get("date")) if "date" in cols else None
        sheet_info[name] = {"header_row": header_row, "cols": cols, "max_date": max_date,
                             "row_count": ws.max_row - header_row}

    if not sheet_info:
        run.status = "failed"
        run.error = "No sheet in workbook has a recognizable header row."
        session.flush()
        return run

    # Primary sheet = the one with the most recent data (the current period's extract). Any
    # other sheet with a recognizable header is treated as a stale/duplicate extract and its
    # rows are excluded -- explicitly, and counted, not silently ignored.
    primary_name = max(sheet_info, key=lambda n: (sheet_info[n]["max_date"] or date.min))
    total_rows_in = 0

    for name, info in sheet_info.items():
        ws = wb[name]
        header_row, cols = info["header_row"], info["cols"]
        row_count = ws.max_row - header_row
        total_rows_in += row_count

        if name != primary_name:
            for r in range(header_row + 1, ws.max_row + 1):
                payload = {h: ws.cell(row=r, column=c).value for h, c in cols.items()}
                record, _ = get_or_create_source_record(session, run, path.name, r, payload, source_sheet=name)
                open_quarantine(
                    session, record, "STALE_WORKSHEET_EXCLUDED",
                    f"Sheet '{name}' has max date {info['max_date']} vs primary sheet "
                    f"'{primary_name}' max date {sheet_info[primary_name]['max_date']}. "
                    f"Excluded as a stale/duplicate extract, not ingested.",
                )
                counter.record("quarantined", "STALE_WORKSHEET_EXCLUDED")
            continue

        for r in range(header_row + 1, ws.max_row + 1):
            payload = {h: ws.cell(row=r, column=c).value for h, c in cols.items()}
            record, _ = get_or_create_source_record(session, run, path.name, r, payload, source_sheet=name)

            raw_name = str(payload.get("employee name", "")).strip()
            role = str(payload.get("role", "")).strip()

            match_id, match_reason = _resolve_plantops_name(raw_name, identities)

            if match_id is None:
                open_quarantine(
                    session, record, "PLANTOPS_AMBIGUOUS_NAME_MATCH",
                    f"PlantOps name '{raw_name}' did not resolve to a unique, confident "
                    f"identity match (name-only correlation, no employee ID available).",
                )
                counter.record("quarantined", "PLANTOPS_AMBIGUOUS_NAME_MATCH")
                continue

            identity_id, identity_dept = match_id
            if identity_dept != CONTEXTUAL_DEPARTMENT and match_reason != "email_localpart_exact":
                open_quarantine(
                    session, record, "PLANTOPS_NAME_MATCH_MISSING_ROLE_CONTEXT",
                    f"PlantOps name '{raw_name}' uniquely matches an identity by name, but that "
                    f"identity is not in {CONTEXTUAL_DEPARTMENT} and the match has no other "
                    f"corroborating context. Policy requires role-context corroboration for "
                    f"name-only matches; quarantined for human confirmation rather than guessed.",
                )
                counter.record("quarantined", "PLANTOPS_NAME_MATCH_MISSING_ROLE_CONTEXT")
                continue

            account = upsert_account(
                session, application_id=app.id, native_id=f"plantops:{identity_id}",
                username=raw_name, account_type="human", enabled=True,
                owner_identity_id=identity_id, ownership_status="owned", source_record_id=record.id,
            )
            ent = get_or_create_entitlement(session, application_id=app.id, native_id=f"role:{role}",
                                             name=role, type_="role", privileged=role == "Plant-Ops-Leads")
            ensure_account_entitlement(session, account.id, ent.id, source_record_id=record.id)

            mark_normalized(record)
            counter.record("normalized", "OK")

    counter.rows_in = total_rows_in
    counter.flush(session, run)
    return run


def _resolve_plantops_name(raw_name: str, identities: list) -> tuple[tuple[str, str], str] | tuple[None, None]:
    """Returns ((identity_id, department), reason) or (None, None)."""
    if raw_name.endswith("@"):
        local = raw_name[:-1].lower()
        parts = local.split(".")
        if len(parts) == 2:
            target = f"{parts[0]} {parts[1]}"
            for identity_id, name, dept in identities:
                if normalize_name(name) == target:
                    return (identity_id, dept), "email_localpart_exact"
        return None, None

    # "First L." style: fuzzy ratio against a short initial scores poorly regardless of
    # correctness, so match it deterministically instead -- exact first name + last name
    # starting with the given initial.
    stripped = raw_name.rstrip(".").strip()
    parts = stripped.split()
    if len(parts) == 2 and len(parts[1]) == 1:
        first_norm = normalize_name(parts[0])
        initial = parts[1].lower()
        candidates = [
            (identity_id, dept) for identity_id, name, dept in identities
            if normalize_name(name).startswith(first_norm + " ")
            and normalize_name(name).split()[-1].startswith(initial)
        ]
        if len(candidates) == 1:
            return candidates[0], "first_lastinitial_exact"
        return None, None  # zero or ambiguous multiple -> don't guess

    comparable = normalize_name(_parse_name_variant(raw_name))
    scored = []
    for identity_id, name, dept in identities:
        ratio = fuzz.token_sort_ratio(comparable, normalize_name(name))
        if ratio >= FUZZY_UNIQUE_THRESHOLD:
            scored.append((ratio, identity_id, dept))
    scored.sort(reverse=True)
    if not scored:
        return None, None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 5:
        return None, None  # ambiguous: multiple near-identical name matches
    _, identity_id, dept = scored[0]
    return (identity_id, dept), "fuzzy_name_unique"
