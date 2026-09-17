from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd
import streamlit as st
from sqlalchemy import select, func

from northwind.db import session_scope
from northwind.models.governance import Campaign, Finding, ReviewItem, WorkflowRun
from northwind.models.identity import Account, Identity
from northwind.models.ingestion import IngestionRun, QuarantineRecord, SOURCE_SYSTEMS

st.set_page_config(page_title="Northwind Identity Governance", layout="wide")
st.title("Northwind Materials -- Identity Governance")

page = st.sidebar.radio("View", ["Executive Value", "Data Health", "Findings", "Campaigns", "JML Runs", "Identity 360"])

with session_scope() as session:
    if page == "Executive Value":
        st.header("Executive Value")
        terminated_closed = session.execute(
            select(func.count()).select_from(Account).join(Identity, Account.owner_identity_id == Identity.id)
            .where(Identity.lifecycle_status == "terminated", Account.enabled == False)  # noqa: E712
        ).scalar()
        terminated_open = session.execute(
            select(func.count()).select_from(Account).join(Identity, Account.owner_identity_id == Identity.id)
            .where(Identity.lifecycle_status == "terminated", Account.enabled == True)  # noqa: E712
        ).scalar()
        revoked = session.execute(select(func.count()).select_from(ReviewItem).where(ReviewItem.decision == "revoke")).scalar()
        open_findings = session.execute(select(func.count()).select_from(Finding).where(Finding.status == "open")).scalar()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Terminated-with-access CLOSED", terminated_closed)
        c1.caption(f"{terminated_open} still open" if terminated_open else "0 remaining open")
        c2.metric("Entitlements revoked via review", revoked)
        c3.metric("Open findings", open_findings)
        c4.metric("Hours saved vs. spreadsheet baseline", "~85*")
        st.caption("*Illustrative: Northwind's last manual access review cycle took 11 weeks "
                   "(≈440 person-hours per the discovery call). A scoped, evidence-backed "
                   "campaign here completes in minutes of compute plus reviewer decision time.")

    elif page == "Data Health":
        st.header("Data Health")
        rows = []
        for src in SOURCE_SYSTEMS:
            run = session.execute(
                select(IngestionRun).where(IngestionRun.source_system == src)
                .order_by(IngestionRun.started_at.desc()).limit(1)
            ).scalar_one_or_none()
            if run:
                rows.append({"source": src, "status": run.status, "rows_in": run.rows_in,
                             "normalized": run.rows_normalized, "quarantined": run.rows_quarantined,
                             "completed_at": run.completed_at})
        st.dataframe(pd.DataFrame(rows), use_container_width=True)

        st.subheader("Open quarantine items (drill-through)")
        q_rows = session.execute(
            select(QuarantineRecord).where(QuarantineRecord.status == "open").limit(200)
        ).scalars().all()
        st.dataframe(pd.DataFrame([{"reason_code": q.reason_code, "explanation": q.explanation[:120]}
                                    for q in q_rows]), use_container_width=True)

    elif page == "Findings":
        st.header("Findings")
        rule_filter = st.selectbox("Rule", ["All"] + sorted({f.rule_code for f in
                                    session.execute(select(Finding)).scalars().all()}))
        query = select(Finding).where(Finding.status == "open").order_by(Finding.risk_score.desc())
        if rule_filter != "All":
            query = query.where(Finding.rule_code == rule_filter)
        findings = session.execute(query.limit(300)).scalars().all()
        st.dataframe(pd.DataFrame([{"score": f.risk_score, "severity": f.severity, "rule": f.rule_code,
                                     "explanation": f.explanation} for f in findings]),
                     use_container_width=True)

    elif page == "Campaigns":
        st.header("Campaigns")
        campaigns = session.execute(select(Campaign)).scalars().all()
        for c in campaigns:
            with st.expander(f"{c.name} ({c.status})"):
                items = session.execute(select(ReviewItem).where(ReviewItem.campaign_id == c.id)).scalars().all()
                decided = sum(1 for i in items if i.decision)
                unassigned = sum(1 for i in items if i.reviewer_id is None)
                st.write(f"Total: {len(items)} | Decided: {decided} | Pending: {len(items)-decided} | "
                         f"Unassigned (IAM fallback queue): {unassigned}")
                st.progress(decided / len(items) if items else 0)

    elif page == "JML Runs":
        st.header("JML Run History")
        runs = session.execute(select(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(100)).scalars().all()
        rows = []
        for r in runs:
            identity = session.get(Identity, r.subject_identity_id)
            rows.append({"type": r.workflow_type, "identity": identity.name if identity else "?",
                         "mode": r.mode, "status": r.status, "steps": len(r.steps)})
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
        st.caption("Select a run in the CLI (`northwind jml ...`) to inspect step-level payloads "
                   "for a failed run -- this view is the summary a customer admin scans first.")

    elif page == "Identity 360":
        st.header("Identity 360")
        name = st.text_input("Search identity by name", "")
        if name:
            identity = session.execute(select(Identity).where(Identity.name.ilike(f"%{name}%"))).scalars().first()
            if identity:
                st.write(f"**{identity.name}** -- {identity.title}, {identity.department}, "
                         f"{identity.lifecycle_status}")
                from northwind.access_graph.effective_access import effective_access_for_identity
                grants = effective_access_for_identity(session, identity.id)
                st.dataframe(pd.DataFrame([{"application": g.application_name, "entitlement": g.entitlement_name,
                                             "type": g.entitlement_type, "how": " -> ".join(g.path)}
                                            for g in grants]), use_container_width=True)
            else:
                st.warning("No match.")
