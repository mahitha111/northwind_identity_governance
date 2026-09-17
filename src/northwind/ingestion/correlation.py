from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from northwind.ingestion.normalize import is_corporate_domain, normalize_name
from northwind.models.identity import Identity, IdentityAlias

# ---------------------------------------------------------------------------
# Correlation policy
#
# This is our own calibrated weighting, informed by (but not identical to) the illustrative
# weights in the delivery blueprint. We document the deviation here rather than silently
# diverging: the blueprint's table, taken literally, makes it impossible for ANY single piece
# of evidence to reach the 80-point auto-match bar (max single item is 60), which would force
# every account into manual review regardless of how strong the identifier evidence is. We
# treat a single unique, deterministic identifier match (employee ID, or an email/UPN local
# part against a *known corporate domain*) as strong primary evidence, and treat name
# agreement as corroboration on top of it. This is more permissive than a literal reading of
# the blueprint table, and precision/recall against ground_truth.json (reported honestly in
# the README once A1+A2 are frozen) is the check on whether that call was correct.
# ---------------------------------------------------------------------------

WEIGHT_EMPLOYEE_ID_MATCH = 65
WEIGHT_CORPORATE_LOCALPART_MATCH = 60
WEIGHT_NONCORPORATE_LOCALPART_MATCH = 40
WEIGHT_EXACT_NAME_CORROBORATION = 20
WEIGHT_FUZZY_NAME_CORROBORATION = 15
FUZZY_NAME_THRESHOLD = 90

AUTO_MATCH_THRESHOLD = 80
QUARANTINE_FLOOR = 50


@dataclass
class CandidateMatch:
    identity_id: str
    identity_name: str
    score: int
    rule_code: str
    evidence: dict = field(default_factory=dict)


@dataclass
class CorrelationResult:
    decision: str  # "auto_match" | "quarantine" | "unmatched"
    best: Optional[CandidateMatch]
    candidates: list[CandidateMatch]


class CorrelationIndex:
    """In-memory index of known identities, built once per ingestion run from the DB.
    Rebuilding per run (rather than caching across runs) keeps correlation logic simple and
    correct: every run sees the current, post-Zoho-ingestion state of the identity table."""

    def __init__(self, session: Session):
        self._by_localpart: dict[str, list[str]] = {}
        self._by_employee_id: dict[str, list[str]] = {}
        self._names: dict[str, str] = {}

        rows = session.execute(
            select(IdentityAlias.normalized_value, IdentityAlias.alias_type, IdentityAlias.identity_id)
        ).all()
        for value, alias_type, identity_id in rows:
            if alias_type == "email_localpart":
                self._by_localpart.setdefault(value, []).append(identity_id)
            elif alias_type == "employee_id":
                self._by_employee_id.setdefault(value, []).append(identity_id)

        for identity_id, name in session.execute(select(Identity.id, Identity.name)).all():
            self._names[identity_id] = name

    def lookup_localpart_only(self, local_part: str) -> list[str]:
        """Raw local-part candidates with no domain/name weighting applied. Used where a
        source has no reliable domain or display name to corroborate with (AWS IAM
        usernames), so the caller applies its own, documented decision rule."""
        return list(self._by_localpart.get(local_part, []))

    def name_of(self, identity_id: str) -> str:
        return self._names.get(identity_id, "?")

    def resolve(
        self,
        local_part: str,
        domain: str,
        display_name: Optional[str],
        employee_id: Optional[str] = None,
    ) -> CorrelationResult:
        candidates: dict[str, CandidateMatch] = {}
        norm_name = normalize_name(display_name) if display_name else ""

        def add(identity_id: str, score: int, rule_code: str, evidence: dict):
            existing = candidates.get(identity_id)
            if existing is None or score > existing.score:
                candidates[identity_id] = CandidateMatch(
                    identity_id=identity_id,
                    identity_name=self._names.get(identity_id, "?"),
                    score=score,
                    rule_code=rule_code,
                    evidence=evidence,
                )

        # Primary identifier evidence
        primary_ids: set[str] = set()
        if employee_id:
            for iid in self._by_employee_id.get(employee_id, []):
                primary_ids.add(iid)
                add(iid, WEIGHT_EMPLOYEE_ID_MATCH, "EMPLOYEE_ID_MATCH", {"employee_id": employee_id})

        if local_part:
            localpart_hits = self._by_localpart.get(local_part, [])
            weight = WEIGHT_CORPORATE_LOCALPART_MATCH if is_corporate_domain(domain) \
                else WEIGHT_NONCORPORATE_LOCALPART_MATCH
            rule = "CORPORATE_LOCALPART_MATCH" if is_corporate_domain(domain) else "NONCORPORATE_LOCALPART_MATCH"
            for iid in localpart_hits:
                primary_ids.add(iid)
                add(iid, weight, rule, {"local_part": local_part, "domain": domain})

        # Name corroboration, applied on top of primary identifier candidates only --
        # per policy, name evidence alone (fuzzy or exact) is never sufficient by itself.
        for iid in list(primary_ids):
            known_name = self._names.get(iid, "")
            known_norm = normalize_name(known_name)
            if norm_name and known_norm == norm_name:
                c = candidates[iid]
                add(iid, c.score + WEIGHT_EXACT_NAME_CORROBORATION, c.rule_code + "+EXACT_NAME",
                    {**c.evidence, "matched_name": known_name})
            elif norm_name and known_norm:
                ratio = fuzz.ratio(norm_name, known_norm)
                if ratio >= FUZZY_NAME_THRESHOLD:
                    c = candidates[iid]
                    add(iid, c.score + WEIGHT_FUZZY_NAME_CORROBORATION, c.rule_code + "+FUZZY_NAME",
                        {**c.evidence, "matched_name": known_name, "fuzzy_ratio": ratio})

        ranked = sorted(candidates.values(), key=lambda c: c.score, reverse=True)
        if not ranked:
            return CorrelationResult(decision="unmatched", best=None, candidates=[])

        best = ranked[0]
        # Conflicting evidence: two candidates both clear the quarantine floor with a
        # meaningfully close score -> force quarantine rather than silently picking one.
        if len(ranked) > 1 and ranked[1].score >= QUARANTINE_FLOOR and (best.score - ranked[1].score) < 10:
            return CorrelationResult(decision="quarantine", best=best, candidates=ranked)

        if best.score >= AUTO_MATCH_THRESHOLD:
            return CorrelationResult(decision="auto_match", best=best, candidates=ranked)
        if best.score >= QUARANTINE_FLOOR:
            return CorrelationResult(decision="quarantine", best=best, candidates=ranked)
        return CorrelationResult(decision="unmatched", best=None, candidates=ranked)
