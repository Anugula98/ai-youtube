# backend/app/tasks.py
"""Celery tasks. Each task follows the same shape: load its own DB session
(never reuse a request-scoped session across a task boundary), do the work,
write a JobRun row reflecting the outcome, and re-raise on failure so
Celery's own retry/backoff machinery can act on it too.

This module currently has one real task (run_stage_task, wrapping the
existing synchronous pipeline.run_stage) to prove the queue against
something that already works. Future media tasks (synthesize_voice,
generate_visuals, render_video, upload_to_youtube) should follow this same
pattern -- create a JobRun row before dispatch, update it from within the
task, and never let the task's return value be the only record of what
happened.
"""
from __future__ import annotations
import datetime as dt

from celery import Task

from .celery_app import celery_app
from .database import SessionLocal
from . import models as m
from . import pipeline as pl


class _DBTask(Task):
    """Base class that gives every task a fresh DB session and guarantees
    it's closed, without every task having to repeat that boilerplate."""
    _db = None

    def __call__(self, *args, **kwargs):
        db = SessionLocal()
        try:
            return self.run_with_db(db, *args, **kwargs)
        finally:
            db.close()


def _mark_job(db, job: m.JobRun, status: m.JobStatus, *, result=None, error=None):
    job.status = status
    if status == m.JobStatus.RUNNING:
        job.started_at = dt.datetime.now(dt.timezone.utc)
    if status in (m.JobStatus.SUCCEEDED, m.JobStatus.FAILED):
        job.finished_at = dt.datetime.now(dt.timezone.utc)
    if result is not None:
        job.result = result
    if error is not None:
        job.error = error
    db.commit()


@celery_app.task(bind=True, base=_DBTask, max_retries=3, default_retry_delay=30)
def run_stage_task(self, job_run_id: int, project_id: int):
    db = SessionLocal()
    try:
        job = db.get(m.JobRun, job_run_id)
        project = db.get(m.Project, project_id)
        if not job or not project:
            return  # nothing sensible to do -- caller-side bug, not worth retrying
        _mark_job(db, job, m.JobStatus.RUNNING)
        try:
            stage_run = pl.run_stage(db, project, project.current_stage)
            if project.pipeline_mode == m.PipelineMode.AUTO:
                pl.advance_if_ready(db, project)
            db.commit()
            _mark_job(db, job, m.JobStatus.SUCCEEDED, result={"stage_run_id": stage_run.id, "status": stage_run.status.value})
        except Exception as exc:
            db.rollback()
            _mark_job(db, job, m.JobStatus.FAILED, error=str(exc))
            raise self.retry(exc=exc)
    finally:
        db.close()