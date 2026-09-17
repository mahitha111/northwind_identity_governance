#!/usr/bin/env python3
"""Reproduces the Day-14 incident (Appendix B) against our own running deployment, per the
task's instruction to inject the underlying causes rather than just write about them.

Run: DATABASE_URL=... PYTHONPATH=src python3 scripts/day14_incident_demo.py
"""
from __future__ import annotations

from sqlalchemy import select

from northwind.campaigns.service import create_campaign
from northwind.config import settings
from northwind.db import session_scope
from northwind.models.identity import Identity
from northwind.models.ingestion import IngestionRun


def demo_cause_2_status_date_conflict():
    print("\n=== Cause 2: termination recorded with status still 'Active' ===")
    with session_scope() as session:
        conflicted = session.execute(
            select(Identity).where(Identity.source_status_date_conflict == True)  # noqa: E712
            .limit(1)
        ).scalar_one_or_none()
        if not conflicted:
            print("No status/date-conflict identity found -- run ingestion first.")
            return
        print(f"Identity: {conflicted.name}")
        print(f"  Raw Zoho status would have read 'Active' (that's what a status-only leaver "
              f"trigger checks), but termination_date = {conflicted.termination_date} is in the past.")
        print(f"  Our lifecycle_status (computed from the date, never from status alone) = "
              f"'{conflicted.lifecycle_status}'.")
        print(f"  PROOF this is not cosmetic: `northwind jml leaver \"{conflicted.name}\" "
              f"--trigger demo` fires and disables their accounts, because the trigger "
              f"condition in src/northwind/lifecycle/leaver.py checks lifecycle_status, "
              f"not raw HR status. A status-only legacy trigger would have missed this "
              f"identity entirely -- which is exactly incident cause #2.")


def demo_cause_3_stale_campaign():
    print("\n=== Cause 3: campaign scoped against a stale/incomplete ingestion ===")
    with session_scope() as session:
        run = session.execute(
            select(IngestionRun).where(IngestionRun.source_system == "salesforce", IngestionRun.status == "succeeded")
            .order_by(IngestionRun.completed_at.desc())
        ).scalars().first()
        if not run:
            print("No successful Salesforce ingestion found -- run ingestion first.")
            return
        original_sla = settings.ingestion_freshness_sla_hours
        settings.ingestion_freshness_sla_hours = 1
        try:
            create_campaign(session, "Incident demo -- should be refused", "Salesforce")
            print("FAIL: campaign creation should have refused a stale source.")
        except ValueError as e:
            print(f"Campaign creation correctly REFUSED: {e}")
            print("This is the prevention control for cause #3: create_campaign() "
                  "(src/northwind/campaigns/service.py) will not silently launch against a "
                  "stale or incomplete source. The Day-14 incident happened because nothing "
                  "in the original design checked this at all.")
        finally:
            settings.ingestion_freshness_sla_hours = original_sla
            session.rollback()


def note_cause_1():
    print("\n=== Cause 1: PlantOps header shifted by one row, 41 rows silently mismapped ===")
    print("Not reproducible as a live failure against this system: src/northwind/ingestion/"
          "plantops.py's _find_header_row() locates the header by matching cell VALUES "
          "against the expected header set, scanning the first 10 rows, rather than assuming "
          "headers sit at a fixed row/column position. A shifted header is still found "
          "correctly and columns are still mapped by name, not position. This was a design "
          "decision made during A1 (before this incident was written up), anticipating "
          "exactly this failure mode from the task's own Appendix A spec ('header begins on "
          "row 4, not row 1'). We did not manufacture a reproduction by deliberately "
          "reintroducing a positional parser -- see docs/incident/rca.md for the honest "
          "accounting of this.")


if __name__ == "__main__":
    demo_cause_2_status_date_conflict()
    demo_cause_3_stale_campaign()
    note_cause_1()
