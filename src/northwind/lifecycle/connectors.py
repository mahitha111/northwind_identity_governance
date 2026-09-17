from __future__ import annotations

import random

from northwind.config import settings


class ConnectorError(Exception):
    pass


def call_mock_connector(connector: str, action: str, payload: dict) -> dict:
    """Simulates calling Entra or AD to provision/deprovision access. Fails at the
    configured rate (MOCK_ENTRA_FAILURE_RATE / MOCK_AD_FAILURE_RATE) so the JML saga's
    partial-failure/compensation path is exercisable on demand, not just in theory."""
    rate = settings.mock_entra_failure_rate if connector == "entra" else settings.mock_ad_failure_rate
    if random.random() < rate:
        raise ConnectorError(f"{connector} {action} failed (simulated, rate={rate})")
    return {"connector": connector, "action": action, "status": "ok", "echo": payload}
