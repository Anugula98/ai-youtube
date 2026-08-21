"""Centralized app configuration. Every value here is overridable via
environment variables (or a .env file in local dev) — nothing environment-
specific is hardcoded elsewhere in the app.
"""
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Environment ---
    env: str = "development"  # development | staging | production
    debug: bool = False

    # --- Database ---
    # SQLite by default for local dev; point at Postgres in staging/production, e.g.:
    # postgresql+psycopg2://user:password@host:5432/newsroom
    database_url: str = "sqlite:///./newsroom.db"

    # --- CORS ---
    # Comma-separated list of allowed origins. "*" is only acceptable in development —
    # main.py refuses to start with "*" when env=="production".
    cors_origins: str = "*"

    # --- Auth ---
    # Shared-secret API key required on every /api/* request when set. Leave unset only
    # for local development; production deployments must set this (see .env.example).
    api_key: str | None = None

    # --- LLM ---
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"

    # --- Pipeline defaults ---
    fact_check_threshold: float = 70.0

    # --- File uploads ---
    # Local disk by default (fine for a single-instance deployment or local
    # dev). For a real multi-replica production deployment, point this at a
    # shared/mounted path or swap the storage calls in main.py for an S3
    # (or equivalent object-store) client -- that swap is not done here.
    upload_dir: str = "./uploads"
    max_upload_size_mb: int = 25

    # --- Logging ---
    log_level: str = "INFO"

    # --- Background scheduler ---
    # Off by default (opt-in). Safe to enable on more than one backend
    # replica -- each tick acquires a DB-backed lock (scheduler_lock.py)
    # before acting, so only one replica executes a given tick's actions.
    # See scheduler.py's module docstring for detail.
    enable_scheduler: bool = False
    scheduler_poll_interval_seconds: int = 60

    # --- Topic Discovery grounding (optional) ---
    # Without this, DiscoveryAgent reasons from the LLM's own training-time
    # knowledge. Set to ground proposals in real recent headlines instead.
    # See agents/search_provider.py's module docstring for this provider's
    # honest verification status (implemented, not exercised against a live
    # key in the environment that built this scaffold).
    newsapi_key: str | None = None

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
