from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from northwind.models.identity import (
    Account,
    AccountEntitlement,
    Application,
    Entitlement,
    EntitlementEdge,
    Identity,
)

MAX_TRAVERSAL_DEPTH = 10  # cycle/runaway guard for nested-group and assume-role walks


@dataclass
class AccessGrant:
    account_username: str
    application_name: str
    entitlement_name: str
    entitlement_type: str
    privileged: bool
    grant_type: str  # "direct" | "inherited"
    path: list[str] = field(default_factory=list)  # e.g. ["Plant-Ops-Leads", "Manufacturing-All"]


def effective_access_for_identity(session: Session, identity_id: str) -> list[AccessGrant]:
    """Answers 'show me everything this identity can do, and how they got it': every direct
    grant, plus everything reachable by walking entitlement_edge (nested groups, app-role
    assignments, assume-role chains) from those direct grants. Returns the path, not just the
    destination -- 'she has it through Plant-Ops-Leads, nested inside Manufacturing-All' is the
    whole point, not an afterthought."""
    accounts = session.execute(select(Account).where(Account.owner_identity_id == identity_id)).scalars().all()

    # Preload the edge graph once; it's small (tens of entries), so an in-memory BFS is simpler
    # and faster than N+1 queries per hop.
    edges = session.execute(select(EntitlementEdge)).scalars().all()
    children_of_parent: dict[str, list[str]] = {}
    for e in edges:
        # parent grants access that child inherits -- so walking FROM a directly-held child
        # entitlement UP to its parent(s) is how we find what else it implies.
        children_of_parent.setdefault(e.child_entitlement_id, []).append(e.parent_entitlement_id)

    grants: list[AccessGrant] = []
    for account in accounts:
        app = session.get(Application, account.application_id)
        direct_links = session.execute(
            select(AccountEntitlement).where(AccountEntitlement.account_id == account.id)
        ).scalars().all()

        for link in direct_links:
            ent = session.get(Entitlement, link.entitlement_id)
            grants.append(AccessGrant(
                account_username=account.username, application_name=app.name,
                entitlement_name=ent.name, entitlement_type=ent.type, privileged=ent.privileged,
                grant_type="direct", path=[ent.name],
            ))

            # BFS upward through the nesting/assume-role graph from this direct grant.
            visited = {ent.id}
            frontier = [(ent.id, [ent.name])]
            depth = 0
            while frontier and depth < MAX_TRAVERSAL_DEPTH:
                depth += 1
                next_frontier = []
                for current_id, path_so_far in frontier:
                    for parent_id in children_of_parent.get(current_id, []):
                        if parent_id in visited:
                            continue
                        visited.add(parent_id)
                        parent_ent = session.get(Entitlement, parent_id)
                        new_path = path_so_far + [parent_ent.name]
                        grants.append(AccessGrant(
                            account_username=account.username, application_name=app.name,
                            entitlement_name=parent_ent.name, entitlement_type=parent_ent.type,
                            privileged=parent_ent.privileged, grant_type="inherited", path=new_path,
                        ))
                        next_frontier.append((parent_id, new_path))
                frontier = next_frontier

    return grants


def find_identity_by_name(session: Session, name: str) -> Identity | None:
    return session.execute(
        select(Identity).where(Identity.name.ilike(f"%{name}%"))
    ).scalars().first()
