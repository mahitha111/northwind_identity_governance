from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from northwind.config import settings
from northwind.db import get_session
from northwind.models.ingestion import SOURCE_SYSTEMS, IngestionRun
from northwind.observability.logging import configure_logging, get_logger

configure_logging()
logger = get_logger("northwind.api")

app = FastAPI(title="Northwind Identity Governance", version="0.1.0")


class SourceHealth(BaseModel):
    source_system: str
    last_run_status: Optional[str]
    last_completed_at: Optional[datetime]
    hours_since_last_success: Optional[float]
    stale: bool


class HealthResponse(BaseModel):
    status: str
    environment: str
    database: str
    sources: list[SourceHealth]


@app.get("/health", response_model=HealthResponse)
def health(session: Session = Depends(get_session)) -> HealthResponse:
    """Reports dependency state and the last successful ingestion per source, per source.
    A customer admin (or a reviewer) should be able to hit this and know, without asking
    anyone, whether the system is trustworthy right now."""
    db_ok = True
    try:
        session.execute(select(func.now()))
    except Exception:  # noqa: BLE001
        db_ok = False

    sources = []
    now = datetime.now(timezone.utc)
    for src in SOURCE_SYSTEMS:
        last_success = session.execute(
            select(IngestionRun)
            .where(IngestionRun.source_system == src, IngestionRun.status == "succeeded")
            .order_by(IngestionRun.completed_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        last_any = session.execute(
            select(IngestionRun)
            .where(IngestionRun.source_system == src)
            .order_by(IngestionRun.started_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        hours_since = None
        stale = True
        if last_success and last_success.completed_at:
            completed = last_success.completed_at
            if completed.tzinfo is None:
                completed = completed.replace(tzinfo=timezone.utc)
            hours_since = (now - completed).total_seconds() / 3600
            stale = hours_since > settings.ingestion_freshness_sla_hours

        sources.append(SourceHealth(
            source_system=src,
            last_run_status=last_any.status if last_any else None,
            last_completed_at=last_success.completed_at if last_success else None,
            hours_since_last_success=hours_since,
            stale=stale,
        ))

    overall = "healthy" if db_ok and not any(s.stale for s in sources) else "degraded"
    return HealthResponse(
        status=overall,
        environment=settings.environment,
        database="up" if db_ok else "down",
        sources=sources,
    )


@app.get("/")
def root():
    return {"service": "northwind", "docs": "/docs", "health": "/health"}
