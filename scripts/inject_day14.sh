#!/usr/bin/env bash
# Runs the full Day-14 incident demonstration against the current database:
# ingests baseline data if needed, then reproduces incident causes #2 and #3 live,
# and prints the honest note on cause #1. See docs/incident/rca.md for the write-up.
set -euo pipefail
cd "$(dirname "$0")/.."

export PYTHONPATH=src

if [ -z "${DATABASE_URL:-}" ]; then
    export DATABASE_URL="postgresql+psycopg://northwind:northwind@localhost:5432/northwind"
fi

if [ ! -f "seed/baseline/zoho_people_workers.csv" ]; then
    echo "[inject_day14] No baseline seed data found -- generating..."
    python3 seed/generate.py --out-dir seed/baseline
fi

echo "[inject_day14] Ensuring base data is ingested..."
python3 -m northwind.cli.main ingest all --input-dir seed/baseline

echo "[inject_day14] Running incident reproduction..."
python3 scripts/day14_incident_demo.py
