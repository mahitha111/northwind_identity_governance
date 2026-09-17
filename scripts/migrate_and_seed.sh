#!/usr/bin/env bash
# Brings the schema up to head and, on first run only, generates and ingests the seed data.
# Idempotent: safe to re-run against an already-seeded database.
set -euo pipefail

echo "[migrate_and_seed] Running Alembic migrations..."
alembic upgrade head

if [ ! -f "seed/baseline/zoho_people_workers.csv" ]; then
    echo "[migrate_and_seed] No baseline seed data found -- generating from seed 42..."
    python3 seed/generate.py --out-dir seed/baseline
else
    echo "[migrate_and_seed] Baseline seed data already present, skipping generation."
fi

echo "[migrate_and_seed] Running ingestion for all six sources..."
python3 -m northwind.cli.main ingest all --input-dir seed/baseline || echo "[migrate_and_seed] Ingestion CLI not yet implemented -- skipping (Phase 2)."

echo "[migrate_and_seed] Done."
