from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

EXCEL_EPOCH = date(1899, 12, 30)
FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def parse_flexible_date(raw: str) -> tuple[Optional[date], Optional[str]]:
    """Parse a date that may arrive as ISO (YYYY-MM-DD), US-slash (MM/DD/YYYY), or an Excel
    serial number. Returns (parsed_date, error_reason). Never raises -- callers decide whether
    a parse failure is quarantine-worthy."""
    if raw is None:
        return None, None
    raw = raw.strip()
    if raw == "":
        return None, None

    # Excel serial number: a bare integer in a plausible range (roughly 1990-2100)
    if raw.isdigit():
        serial = int(raw)
        if 30000 < serial < 80000:
            try:
                return EXCEL_EPOCH + timedelta(days=serial), None
            except OverflowError:
                return None, f"EXCEL_SERIAL_OUT_OF_RANGE:{raw}"

    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date(), None
        except ValueError:
            continue

    return None, f"UNPARSEABLE_DATE:{raw}"


def parse_windows_filetime(raw: str) -> tuple[Optional[datetime], Optional[str]]:
    if raw is None or raw.strip() == "":
        return None, None
    try:
        ticks = int(raw)
    except ValueError:
        return None, f"UNPARSEABLE_FILETIME:{raw}"
    try:
        return FILETIME_EPOCH + timedelta(microseconds=ticks / 10), None
    except OverflowError:
        return None, f"FILETIME_OUT_OF_RANGE:{raw}"
