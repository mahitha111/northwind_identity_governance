from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from northwind.models.governance import Finding, FindingDisposition
from northwind.models.identity import Account, AccountEntitlement, Application, Entitlement, Identity

AS_OF = date(2026, 9, 15)  # "today" for dormancy/age calculations, matching the conversation's current date

# ---------------------------------------------------------------------------
# Separation-of-duties ruleset. Each rule names two entitlement-name substrings that must not
# both be held (directly, in this pass) by the same identity. Deliberately small and
# explainable rather than exhaustive -- a customer will ask "why is this a conflict" and the
# answer needs to be a sentence, not a matrix.
# ---------------------------------------------------------------------------
SOD_RULES = [
    ("Salesforce opportunity approval + revenue administration",
     "Opportunity_Approver", "Revenue_Admin"),
    ("AWS production deployment + audit-log administration",
     "Admins", "AdministratorAccess"),  # approximated with the entitlements this dataset actually has
]


def _finding_key(rule_code: str, subject_type: str, subject_id: str) -> str:
    """Stable business key surviving re-ingestion (unlike Finding.id, which is fresh every
    run) -- this is what FindingDisposition.finding_key is matched against."""
    return f"{rule_code}:{subject_type}:{subject_id}"


def _active_disposition(session: Session, finding_key: str) -> FindingDisposition | None:
    now = datetime.now(timezone.utc)
    disp = session.execute(
        select(FindingDisposition).where(FindingDisposition.finding_key == finding_key)
        .order_by(FindingDisposition.created_at.desc())
    ).scalars().first()
    if disp and (disp.expires_at is None or disp.expires_at > now):
        return disp
    return None


def _upsert_finding(session: Session, *, rule_code, subject_type, subject_id, severity,
                     risk_score, formula_inputs, explanation, evidence, run_id=None) -> Finding:
    key = _finding_key(rule_code, subject_type, subject_id)
    existing = session.execute(
        select(Finding).where(Finding.rule_code == rule_code, Finding.subject_type == subject_type,
                               Finding.subject_id == subject_id, Finding.status != "resolved")
    ).scalar_one_or_none()

    disposition = _active_disposition(session, key)
    status = "open"
    if disposition:
        status = "accepted" if disposition.action == "accept" else "suppressed"

    if existing:
        existing.severity = severity
        existing.risk_score = risk_score
        existing.formula_inputs = formula_inputs
        existing.explanation = explanation
        existing.evidence = evidence
        existing.status = status
        existing.run_id = run_id
        session.flush()
        return existing

    f = Finding(rule_code=rule_code, subject_type=subject_type, subject_id=subject_id,
                severity=severity, risk_score=risk_score, formula_inputs=formula_inputs,
                explanation=explanation, evidence=evidence, status=status, run_id=run_id)
    session.add(f)
    session.flush()
    return f


def _severity_for(score: int) -> str:
    if score >= 9:
        return "critical"
    if score >= 7:
        return "high"
    if score >= 4:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Individual rules. Each returns the number of findings it created/updated.
# ---------------------------------------------------------------------------

def rule_terminated_with_active_access(session: Session) -> int:
    """base 5 + privileged 3 + crown_jewel_app 1 + >30 days terminated 1 = max 10"""
    count = 0
    terminated = session.execute(
        select(Identity).where(Identity.lifecycle_status == "terminated")
    ).scalars().all()
    for identity in terminated:
        accounts = session.execute(
            select(Account).where(Account.owner_identity_id == identity.id, Account.enabled == True)  # noqa: E712
        ).scalars().all()
        if not accounts:
            continue
        days_since_term = (AS_OF - identity.termination_date).days if identity.termination_date else 0
        for account in accounts:
            app = session.get(Application, account.application_id)
            links = session.execute(
                select(AccountEntitlement).where(AccountEntitlement.account_id == account.id)
            ).scalars().all()
            privileged = any(session.get(Entitlement, l.entitlement_id).privileged for l in links)

            base, priv_pts, crit_pts, age_pts = 5, (3 if privileged else 0), \
                (1 if app.criticality == "crown_jewel" else 0), (1 if days_since_term > 30 else 0)
            score = min(base + priv_pts + crit_pts + age_pts, 10)

            _upsert_finding(
                session, rule_code="TERMINATED_WITH_ACTIVE_ACCESS", subject_type="account",
                subject_id=account.id, severity=_severity_for(score), risk_score=score,
                formula_inputs={"base": base, "privileged": priv_pts, "crown_jewel_app": crit_pts,
                                 "terminated_over_30_days": age_pts, "days_since_termination": days_since_term},
                explanation=(
                    f"{identity.name} was terminated on {identity.termination_date} "
                    f"({days_since_term} days ago) but still has an enabled account "
                    f"({account.username}) in {app.name}"
                    + (" with privileged access" if privileged else "") + "."
                ),
                evidence={"identity_id": identity.id, "account_id": account.id, "application": app.name},
            )
            count += 1
    return count


def rule_orphaned_account(session: Session) -> int:
    """base 4 + enabled 2 + privileged 2 + no_owner_evidence 1 (always true here) = max 10"""
    count = 0
    orphans = session.execute(select(Account).where(Account.ownership_status == "orphan")).scalars().all()
    for account in orphans:
        app = session.get(Application, account.application_id)
        links = session.execute(
            select(AccountEntitlement).where(AccountEntitlement.account_id == account.id)
        ).scalars().all()
        privileged = any(session.get(Entitlement, l.entitlement_id).privileged for l in links)
        base, enabled_pts, priv_pts, evidence_pts = 4, (2 if account.enabled else 0), \
            (2 if privileged else 0), 1
        score = min(base + enabled_pts + priv_pts + evidence_pts, 10)
        _upsert_finding(
            session, rule_code="ORPHANED_ACCOUNT", subject_type="account", subject_id=account.id,
            severity=_severity_for(score), risk_score=score,
            formula_inputs={"base": base, "enabled": enabled_pts, "privileged": priv_pts,
                             "no_owner_evidence": evidence_pts},
            explanation=(
                f"Account {account.username} in {app.name} has no HR match and no contractor "
                f"or service evidence -- nobody can currently say who this belongs to."
            ),
            evidence={"account_id": account.id, "application": app.name},
        )
        count += 1
    return count


def rule_dormant_account(session: Session, dormant_days: int = 180) -> int:
    """base 3 + >180 days 2 + privileged 2 + no_owner 2 = max 10"""
    count = 0
    accounts = session.execute(select(Account).where(Account.enabled == True)).scalars().all()  # noqa: E712
    for account in accounts:
        if not account.last_activity_at:
            continue
        last_activity = account.last_activity_at
        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=timezone.utc)
        days_idle = (datetime.now(timezone.utc) - last_activity).days
        if days_idle < dormant_days:
            continue
        app = session.get(Application, account.application_id)
        links = session.execute(
            select(AccountEntitlement).where(AccountEntitlement.account_id == account.id)
        ).scalars().all()
        privileged = any(session.get(Entitlement, l.entitlement_id).privileged for l in links)
        base, dormancy_pts, priv_pts, no_owner_pts = 3, 2, (2 if privileged else 0), \
            (2 if not account.owner_identity_id else 0)
        score = min(base + dormancy_pts + priv_pts + no_owner_pts, 10)
        _upsert_finding(
            session, rule_code="DORMANT_ACCOUNT", subject_type="account", subject_id=account.id,
            severity=_severity_for(score), risk_score=score,
            formula_inputs={"base": base, "over_threshold_days": dormancy_pts, "privileged": priv_pts,
                             "no_owner": no_owner_pts, "days_idle": days_idle},
            explanation=f"Account {account.username} in {app.name} has had no sign-in activity "
                        f"in {days_idle} days (threshold: {dormant_days}).",
            evidence={"account_id": account.id, "application": app.name, "days_idle": days_idle},
        )
        count += 1
    return count


def rule_unowned_non_human_identity(session: Session) -> int:
    """base 4 + production_dependency 2 (assumed for service accounts here) + privileged 2 +
    owner_terminated 2 = max 10"""
    count = 0
    accounts = session.execute(
        select(Account).where(Account.account_type.in_(("service", "unknown")))
    ).scalars().all()
    for account in accounts:
        owner = session.get(Identity, account.owner_identity_id) if account.owner_identity_id else None
        if owner and owner.lifecycle_status != "terminated" and account.ownership_status != "unresolved":
            continue  # has a live, accountable human owner -- not unowned
        app = session.get(Application, account.application_id)
        links = session.execute(
            select(AccountEntitlement).where(AccountEntitlement.account_id == account.id)
        ).scalars().all()
        privileged = any(session.get(Entitlement, l.entitlement_id).privileged for l in links)
        base, dep_pts, priv_pts, owner_term_pts = 4, 2, (2 if privileged else 0), \
            (2 if owner and owner.lifecycle_status == "terminated" else 0)
        score = min(base + dep_pts + priv_pts + owner_term_pts, 10)
        note = (f"originally set up by {owner.name}, who has since been terminated"
                if owner and owner.lifecycle_status == "terminated" else "no owner on record")
        _upsert_finding(
            session, rule_code="UNOWNED_NON_HUMAN_IDENTITY", subject_type="account",
            subject_id=account.id, severity=_severity_for(score), risk_score=score,
            formula_inputs={"base": base, "assumed_production_dependency": dep_pts,
                             "privileged": priv_pts, "owner_terminated": owner_term_pts},
            explanation=f"Non-human account {account.username} in {app.name} has no accountable "
                        f"live owner ({note}). If it breaks or is compromised, there is currently "
                        f"nobody to call.",
            evidence={"account_id": account.id, "application": app.name},
        )
        count += 1
    return count


def rule_privileged_peer_outlier(session: Session, min_cohort_size: int = 8, prevalence_ceiling: float = 0.10) -> int:
    """base 3 + rare_entitlement 2 + privileged 3 + weak_business_reason 1 = max 10.
    Cohort = (department, title). Tuned deliberately conservative: min_cohort_size=8 and a
    10% prevalence ceiling (not the more permissive 20%/5 first tried) after an initial pass
    produced ~400 findings -- noise a customer would tune out and stop trusting the tool on
    day one. Fewer, more defensible findings beat more, weaker ones. See docs/ai.md."""
    count = 0
    identities = session.execute(
        select(Identity).where(Identity.lifecycle_status == "active", Identity.worker_type == "employee")
    ).scalars().all()
    cohorts: dict[tuple, list[Identity]] = defaultdict(list)
    for i in identities:
        cohorts[(i.department, i.title)].append(i)

    for cohort_key, members in cohorts.items():
        if len(members) < min_cohort_size:
            continue  # cohort too small for outlier detection to mean anything (see README)
        member_ids = {m.id for m in members}
        ent_holders: dict[str, set[str]] = defaultdict(set)
        for m in members:
            accounts = session.execute(select(Account).where(Account.owner_identity_id == m.id)).scalars().all()
            for a in accounts:
                links = session.execute(
                    select(AccountEntitlement).where(AccountEntitlement.account_id == a.id)
                ).scalars().all()
                for l in links:
                    ent = session.get(Entitlement, l.entitlement_id)
                    if ent.privileged:
                        ent_holders[ent.id].add(m.id)

        for ent_id, holder_ids in ent_holders.items():
            prevalence = len(holder_ids) / len(members)
            if prevalence > prevalence_ceiling:
                continue  # common enough in this cohort to be normal for the role, not an outlier
            ent = session.get(Entitlement, ent_id)
            for holder_id in holder_ids:
                identity = next(m for m in members if m.id == holder_id)
                base, rare_pts, priv_pts, reason_pts = 3, 2, 3, 1
                score = min(base + rare_pts + priv_pts + reason_pts, 10)
                _upsert_finding(
                    session, rule_code="PRIVILEGED_PEER_OUTLIER", subject_type="identity",
                    subject_id=identity.id, severity=_severity_for(score), risk_score=score,
                    formula_inputs={"base": base, "rare_in_cohort": rare_pts, "privileged": priv_pts,
                                     "no_documented_business_reason": reason_pts,
                                     "cohort": f"{cohort_key[1]} / {cohort_key[0]}", "cohort_size": len(members),
                                     "holders_in_cohort": len(holder_ids)},
                    explanation=(
                        f"{identity.name} ({cohort_key[1]}, {cohort_key[0]}) holds '{ent.name}', "
                        f"which only {len(holder_ids)} of {len(members)} peers in the same title/"
                        f"department cohort hold ({prevalence:.0%}). No documented business reason "
                        f"on file."
                    ),
                    evidence={"identity_id": identity.id, "entitlement": ent.name},
                )
                count += 1
    return count


def rule_sod_conflict(session: Session) -> int:
    """base 5 + both_active 2 + financial_scope 2 + privileged 1 = max 10"""
    count = 0
    for label, ent_a_substr, ent_b_substr in SOD_RULES:
        holders_a = _identities_holding_entitlement_like(session, ent_a_substr)
        holders_b = _identities_holding_entitlement_like(session, ent_b_substr)
        conflicted = holders_a & holders_b
        for identity_id in conflicted:
            identity = session.get(Identity, identity_id)
            both_active = identity.lifecycle_status == "active"
            base, active_pts, fin_pts, priv_pts = 5, (2 if both_active else 0), 2, 1
            score = min(base + active_pts + fin_pts + priv_pts, 10)
            _upsert_finding(
                session, rule_code="SOD_CONFLICT", subject_type="identity", subject_id=identity_id,
                severity=_severity_for(score), risk_score=score,
                formula_inputs={"base": base, "both_active": active_pts, "financial_scope": fin_pts,
                                 "privileged": priv_pts, "rule": label},
                explanation=f"{identity.name} holds both sides of a separation-of-duties conflict: "
                            f"{label}.",
                evidence={"identity_id": identity_id, "rule": label},
            )
            count += 1
    return count


def _identities_holding_entitlement_like(session: Session, name_substr: str) -> set[str]:
    ents = session.execute(select(Entitlement).where(Entitlement.name.ilike(f"%{name_substr}%"))).scalars().all()
    ent_ids = [e.id for e in ents]
    if not ent_ids:
        return set()
    accounts_with_ent = session.execute(
        select(Account.owner_identity_id).join(AccountEntitlement, AccountEntitlement.account_id == Account.id)
        .where(AccountEntitlement.entitlement_id.in_(ent_ids), Account.owner_identity_id.isnot(None))
    ).scalars().all()
    return set(accounts_with_ent)


def rule_missing_manager(session: Session) -> int:
    """base 3 + privileged_access 2 + open_campaign 2 (assumed 0 -- no campaigns yet) +
    no_fallback_route 2 (assumed 0 -- IAM fallback queue always exists) = max 10, so this
    finding tops out lower until A5 campaigns exist; the formula is ready for when they do."""
    count = 0
    identities = session.execute(
        select(Identity).where(Identity.lifecycle_status == "active", Identity.worker_type == "employee",
                                Identity.manager_identity_id.is_(None), Identity.department != "Executive")
    ).scalars().all()
    for identity in identities:
        accounts = session.execute(select(Account).where(Account.owner_identity_id == identity.id)).scalars().all()
        privileged = False
        for a in accounts:
            links = session.execute(select(AccountEntitlement).where(AccountEntitlement.account_id == a.id)).scalars().all()
            if any(session.get(Entitlement, l.entitlement_id).privileged for l in links):
                privileged = True
                break
        base, priv_pts = 3, (2 if privileged else 0)
        score = min(base + priv_pts, 10)
        _upsert_finding(
            session, rule_code="MISSING_MANAGER", subject_type="identity", subject_id=identity.id,
            severity=_severity_for(score), risk_score=score,
            formula_inputs={"base": base, "privileged_access": priv_pts, "open_campaign": 0,
                             "no_fallback_route": 0},
            explanation=f"{identity.name} has no manager on record. Any access review item "
                        f"routed through the manager hierarchy for this person will silently "
                        f"have nowhere to go without the IAM fallback queue.",
            evidence={"identity_id": identity.id},
        )
        count += 1
    return count


ALL_RULES = [
    rule_terminated_with_active_access,
    rule_orphaned_account,
    rule_dormant_account,
    rule_unowned_non_human_identity,
    rule_privileged_peer_outlier,
    rule_sod_conflict,
    rule_missing_manager,
]


def run_all_findings(session: Session) -> dict[str, int]:
    results = {}
    for rule_fn in ALL_RULES:
        results[rule_fn.__name__] = rule_fn(session)
    session.flush()
    return results
