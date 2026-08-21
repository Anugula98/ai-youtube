"""Background scheduler (spec sections 3, 20, 32) — the piece that actually
*executes* the schedule, as opposed to scheduling.py which only computes the
proposed slot layout.

Safe to run on more than one backend replica: each tick first acquires a
DB-backed lock (see scheduler_lock.py — a single-row mutex using an atomic
conditional UPDATE, not a database-specific advisory-lock feature, so it
works the same way on SQLite and Postgres) before doing anything. A replica
that can't get the lock returns immediately having taken no action. This is
what makes `ENABLE_SCHEDULER=true` genuinely safe to enable on more than one
replica — tested for the lock-contention and lock-expiry-and-reacquisition
cases specifically (see tests/test_gap_fill.py).

What one tick actually does:
1. Compute today's proposed slot layout (scheduling.generate_daily_slots).
2. For any slot within the next `lookahead_minutes` that has no project
   assigned yet, look for the single highest-opportunity NEW IdeaVaultEntry
   whose content_type matches the slot kind (SHORT slot -> a SHORT idea;
   FULL_VIDEO slot -> any non-SHORT idea).
3. Promote that idea to a Project (reusing the same logic as the
   /idea-vault/{id}/promote endpoint), schedule it at the slot time, and --
   only if the project's pipeline_mode ends up AUTO -- kick off the full
   pipeline run immediately so it has a chance to reach READY_TO_PUBLISH
   before its scheduled time.

This does NOT publish anything by itself -- publishing (actually pushing to
YouTube) is out of scope for this scaffold entirely (see README's "what's
real vs stubbed" table). The tick only fills open slots with queued ideas
and runs them through the pipeline.
"""
from __future__ import annotations
import datetime as dt
import logging
from typing import List, Dict, Any

from sqlalchemy.orm import Session

from . import models as m
from . import scheduling as sched
from . import pipeline as pl
from . import scheduler_lock

logger = logging.getLogger("newsroom.scheduler")


def run_scheduler_tick(db: Session, now: dt.datetime | None = None, lookahead_minutes: int = 15,
                        use_lock: bool = True) -> List[Dict[str, Any]]:
    """Runs one scheduler tick. When use_lock=True (the default — callers
    that already hold an external guarantee of single-execution, like the
    tests in this file, pass False), the tick first attempts to acquire the
    DB-backed scheduler_lock (see scheduler_lock.py) and returns immediately
    with no actions if another replica currently holds it. This is what
    makes ENABLE_SCHEDULER=true safe on more than one backend replica."""
    if use_lock:
        holder = scheduler_lock.try_acquire(db)
        if holder is None:
            logger.debug("Scheduler tick skipped — another replica holds the lock")
            return []
        try:
            return _run_tick(db, now, lookahead_minutes)
        finally:
            scheduler_lock.release(db, holder)
    return _run_tick(db, now, lookahead_minutes)


def _run_tick(db: Session, now: dt.datetime | None, lookahead_minutes: int) -> List[Dict[str, Any]]:
    now = now or dt.datetime.now()
    config = db.query(m.ScheduleConfig).first()
    if not config:
        return []

    slots = sched.generate_daily_slots(config, now.date())
    window_end = now + dt.timedelta(minutes=lookahead_minutes)

    upcoming_open_slots = [
        s for s in slots
        if now <= dt.datetime.fromisoformat(s["time"]) <= window_end
    ]
    if not upcoming_open_slots:
        return []

    scheduled_today = (
        db.query(m.Project)
        .filter(
            m.Project.scheduled_publish_at >= dt.datetime.combine(now.date(), dt.time.min),
            m.Project.scheduled_publish_at <= dt.datetime.combine(now.date(), dt.time.max),
        )
        .all()
    )
    upcoming_open_slots = sched.annotate_slots_with_projects(upcoming_open_slots, scheduled_today)
    upcoming_open_slots = [s for s in upcoming_open_slots if s["project"] is None]

    actions = []
    for slot in upcoming_open_slots:
        wants_short = slot["content_kind"] == "SHORT"
        idea_query = db.query(m.IdeaVaultEntry).filter(m.IdeaVaultEntry.status == "NEW")
        if wants_short:
            idea_query = idea_query.filter(m.IdeaVaultEntry.content_type == m.ContentType.SHORT)
        else:
            idea_query = idea_query.filter(
                (m.IdeaVaultEntry.content_type != m.ContentType.SHORT) | (m.IdeaVaultEntry.content_type.is_(None))
            )
        idea = idea_query.order_by(m.IdeaVaultEntry.opportunity_score.desc()).first()
        if not idea:
            continue

        project = m.Project(
            title=idea.suggested_title or idea.topic,
            topic=idea.topic,
            content_type=idea.content_type or (m.ContentType.SHORT if wants_short else m.ContentType.NEWS),
            priority=idea.priority,
            pipeline_mode=m.PipelineMode.AUTO,
            current_stage=m.Stage.AV,
            pipeline_state=m.PipelineState.NOT_STARTED,
            scheduled_publish_at=dt.datetime.fromisoformat(slot["time"]),
        )
        db.add(project)
        idea.status = "QUEUED"
        db.flush()

        logger.info("Scheduler tick: promoted idea id=%s to project id=%s for slot %s",
                    idea.id, project.id, slot["time"])
        pl.run_full_pipeline_auto(db, project)
        db.commit()

        actions.append({"slot": slot["time"], "content_kind": slot["content_kind"],
                         "project_id": project.id, "idea_id": idea.id,
                         "pipeline_state": project.pipeline_state.value})

    return actions
