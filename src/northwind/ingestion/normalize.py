from __future__ import annotations

import re
import unicodedata

CORPORATE_DOMAINS = {"northwindmaterials.com", "northwindmaterials.onmicrosoft.com"}


def normalize_employee_id(raw: str) -> str:
    """Trim whitespace, uppercase the E-prefix, and normalize to E##### form by comparing
    the numeric component only. 'E0042', 'E42', '42', 'E00042  ' all normalize to 'E00042'.
    The original raw value should always be retained separately for provenance."""
    if raw is None:
        return ""
    cleaned = raw.strip().upper()
    digits = re.sub(r"\D", "", cleaned)
    if not digits:
        return cleaned
    return f"E{int(digits):05d}"


def normalize_name(raw: str) -> str:
    """Lowercase, strip accents, collapse whitespace, drop punctuation -- for exact
    normalized-name comparison. Deliberately not fuzzy; fuzzy matching is a separate step."""
    if raw is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", raw)
    ascii_ish = "".join(c for c in decomposed if not unicodedata.combining(c))
    ascii_ish = ascii_ish.lower()
    ascii_ish = re.sub(r"[^a-z0-9\s]", " ", ascii_ish)
    return " ".join(ascii_ish.split())


def email_local_part(raw: str) -> tuple[str, str]:
    """Return (local_part_lowercased, domain_lowercased) for an email/UPN. Robust to a
    trailing '@' with nothing after it (one PlantOps-style name variant does this upstream,
    though it should never reach this function since PlantOps has no email field)."""
    if raw is None or "@" not in raw:
        return raw.strip().lower() if raw else "", ""
    local, _, domain = raw.strip().lower().partition("@")
    return local, domain


def is_corporate_domain(domain: str) -> bool:
    return domain in CORPORATE_DOMAINS
