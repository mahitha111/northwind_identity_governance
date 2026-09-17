from __future__ import annotations

import hashlib
import json
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
from northwind.ingestion.repositories import (
    ensure_account_entitlement,
    ensure_entitlement_edge,
    get_or_create_application,
    get_or_create_entitlement,
    upsert_account,
)
from northwind.models.ingestion import IngestionRun

ADMIN_POLICIES = {"AdministratorAccess"}


def ingest_aws(session: Session, path: Path) -> IngestionRun:
    raw_bytes = path.read_bytes()
    input_hash = hashlib.sha256(raw_bytes).hexdigest()
    run = start_run(session, "aws_iam", str(path), input_hash)
    counter = ReconciliationCounter()

    data = json.loads(raw_bytes)
    app = get_or_create_application(session, "AWS IAM", "aws_iam", criticality="high")
    index = CorrelationIndex(session)

    # Roles first, so user->role and role->role (assume-role chain) edges have something to
    # point at regardless of ordering in the source file.
    role_entitlements = {}
    for role in data["roles"]:
        privileged = any(p in ADMIN_POLICIES for p in role.get("attached_policies", []))
        ent = get_or_create_entitlement(
            session, application_id=app.id, native_id=f"role:{role['role_name']}",
            name=role["role_name"], type_="role", privileged=privileged,
        )
        role_entitlements[role["role_name"]] = ent

    for role in data["roles"]:
        chain = role.get("assume_role_chain")
        if chain:
            # chain = [low-privilege caller, ..., high-privilege target]. Each step can assume
            # into the next, so the edge's "parent" is the more-privileged role reached by
            # assuming from "child". This lets a graph walk from the caller find what it can
            # ultimately reach, which is exactly the transitive-admin case A2/A3 need to catch.
            for child_name, parent_name in zip(chain, chain[1:]):
                ensure_entitlement_edge(
                    session, parent_id=role_entitlements[parent_name].id,
                    child_id=role_entitlements[child_name].id, relationship_type="assume_role",
                )

    users = data["users"]
    counter.rows_in = len(users)

    for line_no, user in enumerate(users, start=2):
        record, _ = get_or_create_source_record(session, run, path.name, line_no, user)

        # AWS IAM usernames carry no domain and IAM has no separate display-name field, so we
        # can't apply the standard corporate-domain + name-corroboration scoring here. In this
        # environment usernames follow the same first.last convention as the verified
        # corporate email, so we treat an exact, GLOBALLY UNIQUE local-part match as sufficient
        # on its own -- documented as a deliberate simplification, checked against
        # ground_truth.json precision/recall like every other correlation decision.
        candidates = index.lookup_localpart_only(user["username"])
        owner_identity_id = candidates[0] if len(candidates) == 1 else None
        ownership_status = "owned" if owner_identity_id else "unresolved"

        oldest_key_days = max((k["created_days_ago"] for k in user.get("access_keys", [])), default=0)

        account = upsert_account(
            session, application_id=app.id, native_id=user["arn"], username=user["username"],
            account_type="human" if owner_identity_id else "unknown", enabled=True,
            owner_identity_id=owner_identity_id, ownership_status=ownership_status,
            source_record_id=record.id,
        )

        for group_name in user.get("groups", []):
            ent = get_or_create_entitlement(session, application_id=app.id, native_id=f"group:{group_name}",
                                             name=group_name, type_="group",
                                             privileged=group_name == "Admins")
            ensure_account_entitlement(session, account.id, ent.id, source_record_id=record.id)

        mark_normalized(record)
        if owner_identity_id:
            counter.record("normalized", "OK")
        elif len(candidates) > 1:
            open_quarantine(
                session, record, "CORRELATION_AMBIGUOUS",
                f"AWS IAM username {user['username']} matches more than one identity by "
                f"local part; needs human confirmation rather than a guess.",
                candidate_ids=candidates,
            )
            counter.record("quarantined", "CORRELATION_AMBIGUOUS")
        else:
            open_quarantine(
                session, record, "UNOWNED_NON_HUMAN_IDENTITY",
                f"AWS IAM user {user['username']} has no corresponding human identity "
                f"(access key age: {oldest_key_days} days). Feeds the unowned-non-human-identity "
                f"and stale-credential findings.",
            )
            counter.record("quarantined", "UNOWNED_NON_HUMAN_IDENTITY")

    counter.flush(session, run)
    return run
