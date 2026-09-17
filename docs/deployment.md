# Deployment

## Local (verified in this environment)

This sandbox had no Docker daemon available, so all development and verification in this
submission was done against a locally-installed PostgreSQL 16 instance rather than Docker
Compose:

```bash
apt-get install -y postgresql
service postgresql start
su postgres -c "psql -c \"CREATE USER northwind WITH PASSWORD 'northwind' SUPERUSER;\""
su postgres -c "psql -c \"CREATE DATABASE northwind OWNER northwind;\""
pip install -e ".[dev]" --break-system-packages
export DATABASE_URL="postgresql+psycopg://northwind:northwind@localhost:5432/northwind"
alembic upgrade head
python3 seed/generate.py --out-dir seed/baseline
python3 -m northwind.cli.main ingest all --input-dir seed/baseline
```

## Docker Compose (written, not verified end-to-end in a container in this environment)

`docker-compose.yml` defines postgres -> migrate (one-shot) -> api -> console with health
checks and dependency ordering. It has been reviewed for correctness but **not run inside an
actual Docker daemon** in this environment, since none was available. This is stated plainly
rather than claimed as tested, per the task's own instruction that confident, unverified
claims in a README are a failure condition.

```bash
docker compose up -d
# API:     http://localhost:8000  (docs at /docs, health at /health)
# Console: http://localhost:8501
```

If you have Docker available and this doesn't work exactly as described, that's the one part
of this submission most likely to need a small fix on first real run -- please treat it as
such rather than as evidence the underlying system doesn't work; the underlying system's
correctness is independently verified via the Postgres-direct path above and the test suite.

## Kubernetes / cloud deployment

Not attempted. Explicitly deferred (see README "What we cut") until Compose itself has been
verified in a real container environment -- building Kubernetes manifests against an
unverified Compose file would just propagate the same unverified assumptions one layer
further.

## CI/CD

Not implemented. Given the choice between a CI pipeline and the functional correctness work in
A1-A5, the correctness work was prioritized, since a CI pipeline that runs against an
already-under-tested system doesn't add real assurance.
