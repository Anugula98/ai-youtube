# backend/app/celery_app.py
"""Celery application instance. Workers are started separately from the
FastAPI process:  celery -A app.celery_app worker --loglevel=info
"""
from celery import Celery

from .config import get_settings

settings = get_settings()

celery_app = Celery(
    "newsroom",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # Media-generation/rendering tasks (added in the next Phase 1 slice)
    # will be long-running -- prefetch=1 avoids one worker hoarding several
    # tasks while others sit idle. Fine for today's fast text tasks too.
    worker_prefetch_multiplier=1,
)

# Import task modules so Celery registers them at worker startup.
from . import tasks  # noqa: E402,F401