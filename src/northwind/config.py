from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://northwind:northwind@localhost:5432/northwind"
    log_level: str = "INFO"
    environment: str = "local"

    mock_entra_failure_rate: float = 0.0
    mock_ad_failure_rate: float = 0.0

    ingestion_freshness_sla_hours: int = 24


settings = Settings()
