#!/usr/bin/env python3
"""Exports an auditor-ready evidence pack (CSV) for a given campaign id or name.

Usage: python3 scripts/export_evidence.py "Q3 2026 Salesforce SOX Review" > evidence.csv
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select

from northwind.campaigns.service import evidence_pack
from northwind.db import session_scope
from northwind.models.governance import Campaign


def main():
    if len(sys.argv) != 2:
        print("Usage: export_evidence.py <campaign-id-or-name>", file=sys.stderr)
        sys.exit(1)
    identifier = sys.argv[1]

    with session_scope() as session:
        campaign = session.get(Campaign, identifier)
        if campaign is None:
            campaign = session.execute(
                select(Campaign).where(Campaign.name.ilike(f"%{identifier}%"))
            ).scalars().first()
        if campaign is None:
            print(f"No campaign matches '{identifier}'", file=sys.stderr)
            sys.exit(1)

        rows = evidence_pack(session, campaign.id)

    writer = csv.DictWriter(sys.stdout, fieldnames=[
        "identity", "entitlement", "reviewer_route", "reviewer", "decision", "reason",
        "decided_at", "revocation_executed",
    ])
    writer.writeheader()
    for row in rows:
        writer.writerow(row)


if __name__ == "__main__":
    main()
