"""Application settings, loaded from environment / .env.

All business-rule windows are configurable so reviewers can tune them without code
changes (and so tests can shrink windows to run fast).
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "production"
    log_level: str = "INFO"

    # Async SQLAlchemy DSN. PostgreSQL in docker; sqlite for local/tests.
    database_url: str = "postgresql+asyncpg://apex:apex_secret@db:5432/store_intelligence"

    store_layout_path: str = "data/store_layout.json"
    pos_csv_path: str = "data/pos_transactions.csv"

    # business-rule windows (seconds)
    session_window_s: int = 3600
    pos_correlation_window_s: int = 300
    stale_feed_threshold_s: int = 600
    dead_zone_threshold_s: int = 1800
    dwell_emit_interval_s: int = 30
    min_sessions_for_confidence: int = 20
    conversion_drop_lookback_days: int = 7
    queue_spike_threshold: int = 5  # queue_depth that trips a spike anomaly


@lru_cache
def get_settings() -> Settings:
    return Settings()
