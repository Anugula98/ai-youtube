"""FastAPI app.

Local dev:   uvicorn app.main:app --reload --port 8000
Production:  gunicorn app.main:app -c gunicorn.conf.py   (see that file)

Schema is owned by Alembic migrations (see alembic/), not create_all --
run `alembic upgrade head` before starting the app in any environment.
"""
from __future__ import annotations
import contextvars
import datetime as dt
import logging
import os
import time
import uuid
from pathlib import Path
from typing import List, Dict, Any

_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

from fastapi import FastAPI, Depends, HTTPException, Request, Header, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import models as m
from .models import utcnow
from .database import get_db, engine
from .config import get_settings
from .schemas import (
    ProjectCreate, ProjectOut, ProjectDetailOut, ApprovalIn,
    ScheduleConfigIn, ScheduleConfigOut, IdeaVaultEntryIn, IdeaVaultEntryOut,
    YouTubeConfigIn, YouTubeConfigOut,UserCreate, UserOut, LoginIn, TokenOut, RefreshIn, JobRunOut
)
from .security import hash_password, verify_password, create_access_token, create_refresh_token, decode_token, InvalidTokenError
from .deps import get_current_user, require_project_owner

from . import pipeline as pl
from . import scheduling as sched
from . import scheduler as bg_scheduler

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s",
)
logger = logging.getLogger("newsroom")


class _RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = _request_id_ctx.get()
        return True


# Filters must be attached to the *handler*, not a logger -- during propagation
# from a child logger (e.g. "newsroom") up to root, only handler-level filters
# run; a filter on the root Logger object itself is skipped.
for _handler in logging.getLogger().handlers:
    _handler.addFilter(_RequestIdFilter())

if settings.is_production and settings.cors_origins.strip() == "*":
    raise RuntimeError(
        "CORS_ORIGINS is '*' while ENV=production. Set an explicit comma-separated "
        "origin list in the environment before starting in production."
    )
if settings.is_production and not settings.encryption_key:
    raise RuntimeError(
        "ENCRYPTION_KEY is not set while ENV=production. Generate one and set it "
        "before starting -- see config.py's encryption_key docstring."
    )
if settings.is_production and settings.jwt_secret_key == "CHANGE_ME_INSECURE_DEV_ONLY":
    raise RuntimeError("JWT_SECRET_KEY is still the insecure default while ENV=production.")

app = FastAPI(title="AI YouTube Newsroom API", debug=settings.debug)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Assigns a request id (for log correlation) and logs method/path/status/latency.
    Doesn't log request/response bodies -- those may contain user-supplied topic text."""
    request_id = str(uuid.uuid4())[:8]
    token = _request_id_ctx.set(request_id)
    start = time.time()
    try:
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled exception")
            raise
        duration_ms = round((time.time() - start) * 1000, 1)
        logger.info("%s %s -> %s (%sms)", request.method, request.url.path, response.status_code, duration_ms)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        _request_id_ctx.reset(token)


def require_api_key(x_api_key: str | None = Header(default=None)):
    """No-op when API_KEY isn't configured (local dev). In any environment where
    API_KEY is set, every /api/* request must present it via the X-API-Key header."""
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never leak internal exception details to the client in production.
    if settings.is_production:
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})
    raise exc


@app.get("/")
def root():
    return {
        "name": "AI YouTube Newsroom API",
        "status": "ok",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "health": "/healthz",
        "readiness": "/readyz",
        "ui": "Open ../frontend/index.html locally, or use the frontend service on port 8080 in Docker Compose.",
        "api": "/api",
    }


@app.get("/api")
def api_index():
    return {
        "name": "AI YouTube Newsroom API",
        "version": "scaffold",
        "docs": "/docs",
        "resources": {
            "projects": "/api/projects",
            "today": "/api/today",
            "calendar": "/api/calendar?date=YYYY-MM-DD",
            "idea_vault": "/api/idea-vault",
            "search": "/api/search?q=term",
            "youtube_config": "/api/youtube-config",
        },
    }


# ---------------------------------------------------------------------------
# Background scheduler (opt-in — see app/scheduler.py's module docstring for
# the single-instance-only caveat before enabling this in a multi-replica
# deployment).
# ---------------------------------------------------------------------------
_apscheduler = None

if settings.enable_scheduler:
    from apscheduler.schedulers.background import BackgroundScheduler
    from .database import SessionLocal

    def _scheduler_tick_job():
        db = SessionLocal()
        try:
            actions = bg_scheduler.run_scheduler_tick(db)
            if actions:
                logger.info("Scheduler tick took %d action(s): %s", len(actions), actions)
        except Exception:
            logger.exception("Scheduler tick failed")
        finally:
            db.close()

    _apscheduler = BackgroundScheduler()
    _apscheduler.add_job(_scheduler_tick_job, "interval", seconds=settings.scheduler_poll_interval_seconds)
    _apscheduler.start()
    logger.info("Background scheduler enabled, polling every %ss", settings.scheduler_poll_interval_seconds)


@app.post("/api/scheduler/tick", dependencies=[Depends(require_api_key)])
def trigger_scheduler_tick(db: Session = Depends(get_db)):
    """Manually fires one scheduler tick — useful for testing/ops without
    waiting for the background interval, and the only way to exercise this
    logic at all when ENABLE_SCHEDULER is off."""
    actions = bg_scheduler.run_scheduler_tick(db)
    return {"actions": actions}


@app.get("/healthz")
def healthz():
    """Liveness probe -- no DB dependency, just confirms the process is up."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    """Readiness probe -- confirms the DB connection actually works."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}")


#@app.post("/api/projects", response_model=ProjectOut, dependencies=[Depends(require_api_key)])
@app.post("/api/projects", response_model=ProjectOut)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db),current_user: m.User = Depends(get_current_user)):
    project = m.Project(
        user_id=current_user.id,   
        title=payload.title,
        topic=payload.topic,
        content_type=payload.content_type,
        duration_target_seconds=payload.duration_target_seconds,
        target_audience=payload.target_audience,
        tone=payload.tone,
        language=payload.language,
        user_instructions=payload.user_instructions,
        source_urls=payload.source_urls,
        product_info=payload.product_info,
        priority=payload.priority,
        pipeline_mode=payload.pipeline_mode,
        current_stage=m.Stage.AV,
        pipeline_state=m.PipelineState.NOT_STARTED,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    logger.info("Created project id=%s topic=%r", project.id, project.topic)
    return project


#@app.get("/api/projects", response_model=List[ProjectOut], dependencies=[Depends(require_api_key)])
@app.get("/api/projects", response_model=List[ProjectOut])
def list_projects(db: Session = Depends(get_db), current_user: m.User = Depends(get_current_user)):

    return db.query(m.Project).filter(m.Project.user_id == current_user.id).order_by(m.Project.created_at.desc()).all()



#@app.get("/api/projects/{project_id}", response_model=ProjectDetailOut, dependencies=[Depends(require_api_key)])
@app.get("/api/projects/{project_id}", response_model=ProjectDetailOut)
def get_project(project: m.Project = Depends(require_project_owner)):
    return project


@app.post("/api/projects/{project_id}/run-stage", response_model=ProjectDetailOut, dependencies=[Depends(require_api_key)])
def run_current_stage(project_id: int, db: Session = Depends(get_db)):
    """Runs whatever agent corresponds to the project's current_stage."""
    project = db.get(m.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    pl.run_stage(db, project, project.current_stage)
    if project.pipeline_mode == m.PipelineMode.AUTO:
        pl.advance_if_ready(db, project)
    db.commit()
    db.refresh(project)
    return project


@app.post("/api/projects/{project_id}/approve", response_model=ProjectDetailOut, dependencies=[Depends(require_api_key)])
def approve_stage(project_id: int, payload: ApprovalIn, db: Session = Depends(get_db)):
    project = db.get(m.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if payload.action == "APPROVE":
        pl.approve_current_stage(db, project, payload.note, payload.actor)
    else:
        db.add(m.ApprovalEvent(
            project_id=project.id, stage=project.current_stage,
            action=m.ApprovalAction(payload.action), actor=payload.actor, note=payload.note,
        ))
        if payload.action in ("REJECT",):
            project.pipeline_state = m.PipelineState.REJECTED
        elif payload.action in ("SEND_BACK",):
            project.pipeline_state = m.PipelineState.BLOCKED
    db.commit()
    db.refresh(project)
    return project


@app.post("/api/projects/{project_id}/run-vertical-slice", response_model=ProjectDetailOut, dependencies=[Depends(require_api_key)])
def run_vertical_slice(project_id: int, db: Session = Depends(get_db)):
    """Runs AV -> Research -> Fact Check back-to-back (ignores REVIEW-mode
    pauses at each stage, but still respects the Fact Check score gate) --
    convenience endpoint for demoing/testing the slice end-to-end."""
    project = db.get(m.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    for stage in (m.Stage.AV, m.Stage.RESEARCH, m.Stage.FACT_CHECK):
        pl.run_stage(db, project, stage)
        if project.pipeline_state == m.PipelineState.BLOCKED:
            break
        pl.advance_if_ready(db, project)
    db.commit()
    db.refresh(project)
    return project


@app.post("/api/projects/{project_id}/run-full-pipeline", response_model=ProjectDetailOut, dependencies=[Depends(require_api_key)])
def run_full_pipeline(project_id: int, db: Session = Depends(get_db)):
    """Runs every stage from wherever the project currently is through
    PUBLISH and the Final Quality Gate, stopping early on BLOCKED/NEEDS_REVIEW."""
    project = db.get(m.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    pl.run_full_pipeline_auto(db, project)
    db.commit()
    db.refresh(project)
    return project


@app.get("/api/projects/{project_id}/quality-gate", dependencies=[Depends(require_api_key)])
def get_quality_gate(project_id: int, db: Session = Depends(get_db)):
    result = (
        db.query(m.QualityGateResult)
        .filter(m.QualityGateResult.project_id == project_id)
        .order_by(m.QualityGateResult.created_at.desc())
        .first()
    )
    if not result:
        raise HTTPException(404, "No quality gate result yet — run the pipeline through PUBLISH first")
    return {
        "newsworthiness": result.newsworthiness, "accuracy": result.accuracy,
        "research": result.research, "originality": result.originality,
        "script": result.script, "voice": result.voice, "visuals": result.visuals,
        "copyright": result.copyright, "thumbnail": result.thumbnail, "title": result.title,
        "viewer_value": result.viewer_value, "freshness": result.freshness,
        "overall_score": result.overall_score, "verdict": result.verdict,
        "created_at": result.created_at.isoformat(),
    }


@app.get("/api/projects/{project_id}/claims", dependencies=[Depends(require_api_key)])
def get_claims(project_id: int, db: Session = Depends(get_db)):
    claims = db.query(m.Claim).filter(m.Claim.project_id == project_id).all()
    return [
        {
            "id": c.id, "text": c.text, "claim_type": c.claim_type,
            "verification_status": c.verification_status.value if c.verification_status else None,
            "confidence": c.confidence,
        }
        for c in claims
    ]


@app.get("/api/projects/{project_id}/sources", dependencies=[Depends(require_api_key)])
def get_sources(project_id: int, db: Session = Depends(get_db)):
    sources = db.query(m.Source).filter(m.Source.project_id == project_id).all()
    return [
        {"id": s.id, "url": s.url, "title": s.title, "publisher": s.publisher,
         "source_tier": s.source_tier.value if s.source_tier else None}
        for s in sources
    ]


@app.get("/api/today", dependencies=[Depends(require_api_key)])
def today_metrics(db: Session = Depends(get_db)):
    projects = db.query(m.Project).all()
    return {
        "full_videos": {"target": 1, "done": sum(1 for p in projects if p.pipeline_state == m.PipelineState.PUBLISHED)},
        "in_production": sum(1 for p in projects if p.pipeline_state == m.PipelineState.IN_PROGRESS),
        "needs_review": sum(1 for p in projects if p.pipeline_state == m.PipelineState.NEEDS_REVIEW),
        "ready": sum(1 for p in projects if p.pipeline_state == m.PipelineState.READY_TO_PUBLISH),
        "blocked": sum(1 for p in projects if p.pipeline_state == m.PipelineState.BLOCKED),
        "total_projects": len(projects),
    }


# ---------------------------------------------------------------------------
# Scheduling / Content Calendar (spec sections 3, 4, 20, 32)
# ---------------------------------------------------------------------------

def _get_or_create_schedule_config(db: Session) -> m.ScheduleConfig:
    config = db.query(m.ScheduleConfig).first()
    if not config:
        config = m.ScheduleConfig()
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


@app.get("/api/schedule-config", response_model=ScheduleConfigOut, dependencies=[Depends(require_api_key)])
def get_schedule_config(db: Session = Depends(get_db)):
    return _get_or_create_schedule_config(db)


@app.put("/api/schedule-config", response_model=ScheduleConfigOut, dependencies=[Depends(require_api_key)])
def update_schedule_config(payload: ScheduleConfigIn, db: Session = Depends(get_db)):
    config = _get_or_create_schedule_config(db)
    for field, value in payload.model_dump().items():
        setattr(config, field, value)
    db.commit()
    db.refresh(config)
    return config


def _get_or_create_youtube_config(db: Session) -> m.YouTubeConfig:
    config = db.query(m.YouTubeConfig).first()
    if not config:
        config = m.YouTubeConfig()
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


def _youtube_config_out(config: m.YouTubeConfig) -> YouTubeConfigOut:
    return YouTubeConfigOut(
        id=config.id,
        channel_id=config.channel_id,
        channel_name=config.channel_name,
        default_privacy_status=config.default_privacy_status,
        default_category_id=config.default_category_id,
        default_language=config.default_language,
        default_tags=config.default_tags or [],
        made_for_kids=config.made_for_kids,
        auto_publish_enabled=config.auto_publish_enabled,
        upload_description_footer=config.upload_description_footer,
        has_client_id=bool(config.client_id),
        has_client_secret=bool(config.client_secret),
        has_refresh_token=bool(config.refresh_token),
    )


@app.get("/api/youtube-config", response_model=YouTubeConfigOut, dependencies=[Depends(require_api_key)])
def get_youtube_config(db: Session = Depends(get_db)):
    return _youtube_config_out(_get_or_create_youtube_config(db))


@app.put("/api/youtube-config", response_model=YouTubeConfigOut, dependencies=[Depends(require_api_key)])
def update_youtube_config(payload: YouTubeConfigIn, db: Session = Depends(get_db)):
    config = _get_or_create_youtube_config(db)
    data = payload.model_dump()
    for secret_field in ("client_id", "client_secret", "refresh_token"):
        if not data.get(secret_field):
            data.pop(secret_field, None)
    for field, value in data.items():
        setattr(config, field, value)
    db.commit()
    db.refresh(config)
    return _youtube_config_out(config)


@app.get("/api/calendar", dependencies=[Depends(require_api_key)])
def get_calendar(date: str, db: Session = Depends(get_db)):
    """date: YYYY-MM-DD. Returns the proposed slot layout for that day
    (from the current ScheduleConfig, with weekday/weekend overrides
    applied) annotated with whichever existing projects are scheduled
    near each slot."""
    try:
        target_date = dt.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")

    config = _get_or_create_schedule_config(db)
    slots = sched.generate_daily_slots(config, target_date)

    day_start = dt.datetime.combine(target_date, dt.time.min)
    day_end = dt.datetime.combine(target_date, dt.time.max)
    scheduled_projects = (
        db.query(m.Project)
        .filter(m.Project.scheduled_publish_at >= day_start, m.Project.scheduled_publish_at <= day_end)
        .all()
    )
    slots = sched.annotate_slots_with_projects(slots, scheduled_projects)
    return {"date": date, "slots": slots}


@app.post("/api/projects/{project_id}/schedule", response_model=ProjectOut, dependencies=[Depends(require_api_key)])
def schedule_project(project_id: int, scheduled_publish_at: str, db: Session = Depends(get_db)):
    """Assign (or reassign — spec section 4's drag/reschedule) a project to a
    specific publish datetime. scheduled_publish_at: ISO 8601 datetime."""
    project = db.get(m.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    try:
        project.scheduled_publish_at = dt.datetime.fromisoformat(scheduled_publish_at)
    except ValueError:
        raise HTTPException(400, "scheduled_publish_at must be ISO 8601")
    db.commit()
    db.refresh(project)
    return project


# ---------------------------------------------------------------------------
# Idea Vault (spec section 23)
# ---------------------------------------------------------------------------

@app.get("/api/idea-vault", response_model=List[IdeaVaultEntryOut], dependencies=[Depends(require_api_key)])
def list_idea_vault(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(m.IdeaVaultEntry)
    if status:
        q = q.filter(m.IdeaVaultEntry.status == status)
    return q.order_by(m.IdeaVaultEntry.opportunity_score.desc()).all()


@app.post("/api/idea-vault", response_model=IdeaVaultEntryOut, dependencies=[Depends(require_api_key)])
def create_idea(payload: IdeaVaultEntryIn, db: Session = Depends(get_db)):
    idea = m.IdeaVaultEntry(**payload.model_dump())
    db.add(idea)
    db.commit()
    db.refresh(idea)
    return idea


@app.patch("/api/idea-vault/{idea_id}", response_model=IdeaVaultEntryOut, dependencies=[Depends(require_api_key)])
def update_idea_status(idea_id: int, status: str, db: Session = Depends(get_db)):
    idea = db.get(m.IdeaVaultEntry, idea_id)
    if not idea:
        raise HTTPException(404, "Idea not found")
    idea.status = status
    db.commit()
    db.refresh(idea)
    return idea


@app.post("/api/idea-vault/{idea_id}/promote", response_model=ProjectOut, dependencies=[Depends(require_api_key)])
def promote_idea(idea_id: int, db: Session = Depends(get_db)):
    """Turns an Idea Vault entry into a real Project (current_stage=AV,
    NOT_STARTED) and marks the idea QUEUED."""
    idea = db.get(m.IdeaVaultEntry, idea_id)
    if not idea:
        raise HTTPException(404, "Idea not found")
    project = m.Project(
        title=idea.suggested_title or idea.topic,
        topic=idea.topic,
        content_type=idea.content_type or m.ContentType.NEWS,
        priority=idea.priority,
        pipeline_mode=m.PipelineMode.REVIEW,
        current_stage=m.Stage.AV,
        pipeline_state=m.PipelineState.NOT_STARTED,
    )
    db.add(project)
    idea.status = "QUEUED"
    db.commit()
    db.refresh(project)
    return project


# ---------------------------------------------------------------------------
# Content Repurposing (spec section 22)
# ---------------------------------------------------------------------------

@app.post("/api/projects/{project_id}/repurpose", response_model=List[ProjectOut], dependencies=[Depends(require_api_key)])
def repurpose_into_shorts(project_id: int, db: Session = Depends(get_db)):
    """Reads the Publish stage's shorts_ideas (generated by PublishAgent per
    spec section 22) and materializes each into a new standalone SHORT
    project, queued at AV/NOT_STARTED, linked back via user_instructions."""
    project = db.get(m.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    publish_run = pl._get_current_stage_run(db, project.id, m.Stage.PUBLISH)
    if not publish_run or not publish_run.output.get("shorts_ideas"):
        raise HTTPException(400, "No Shorts ideas available yet — run the Publish stage first")

    created = []
    for idea in publish_run.output["shorts_ideas"]:
        short_project = m.Project(
            title=idea.get("title", f"Short from #{project.id}"),
            topic=idea.get("hook", project.topic),
            content_type=m.ContentType.SHORT,
            duration_target_seconds=45,
            priority=project.priority,
            pipeline_mode=project.pipeline_mode,
            current_stage=m.Stage.AV,
            pipeline_state=m.PipelineState.NOT_STARTED,
            user_instructions=(
                f"Repurposed from project #{project.id} ('{project.title}'), "
                f"source section: {idea.get('source_section', 'unspecified')}."
            ),
        )
        db.add(short_project)
        created.append(short_project)
    db.commit()
    for p in created:
        db.refresh(p)
    return created


# ---------------------------------------------------------------------------
# Search / Filter (spec section 36)
# ---------------------------------------------------------------------------

@app.get("/api/search", dependencies=[Depends(require_api_key)])
def search(
    q: str | None = None,
    content_type: str | None = None,
    pipeline_state: str | None = None,
    priority: str | None = None,
    db: Session = Depends(get_db),
):
    """Searches projects (title/topic), sources (title/url/publisher), and
    claims (text), with optional filters applied to the project search only
    (content_type/pipeline_state/priority are project-level fields)."""
    results: Dict[str, Any] = {"projects": [], "sources": [], "claims": []}

    project_q = db.query(m.Project)
    if q:
        like = f"%{q}%"
        project_q = project_q.filter((m.Project.title.ilike(like)) | (m.Project.topic.ilike(like)))
    if content_type:
        project_q = project_q.filter(m.Project.content_type == content_type)
    if pipeline_state:
        project_q = project_q.filter(m.Project.pipeline_state == pipeline_state)
    if priority:
        project_q = project_q.filter(m.Project.priority == priority)
    results["projects"] = [
        {"id": p.id, "title": p.title, "topic": p.topic, "content_type": p.content_type.value,
         "pipeline_state": p.pipeline_state.value, "priority": p.priority.value}
        for p in project_q.limit(50).all()
    ]

    if q:
        like = f"%{q}%"
        results["sources"] = [
            {"id": s.id, "project_id": s.project_id, "url": s.url, "title": s.title, "publisher": s.publisher}
            for s in db.query(m.Source).filter(
                (m.Source.title.ilike(like)) | (m.Source.url.ilike(like)) | (m.Source.publisher.ilike(like))
            ).limit(50).all()
        ]
        results["claims"] = [
            {"id": c.id, "project_id": c.project_id, "text": c.text,
             "verification_status": c.verification_status.value if c.verification_status else None}
            for c in db.query(m.Claim).filter(m.Claim.text.ilike(like)).limit(50).all()
        ]

    return results


# ---------------------------------------------------------------------------
# File uploads (spec sections 9, 34) + Asset library (spec section 39)
# ---------------------------------------------------------------------------

ALLOWED_UPLOAD_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif",   # photos, screenshots
    ".mp4", ".mov", ".webm",                    # unboxing videos
    ".mp3", ".wav", ".m4a",                     # audio notes
    ".txt", ".md", ".csv", ".json",             # notes / benchmark exports
}


def _safe_upload_path(project_id: int, filename: str) -> Path:
    """Sanitizes the filename (strip any path components -- prevents path
    traversal via a crafted filename like '../../etc/passwd') and returns
    the full destination path under settings.upload_dir/{project_id}/.

    Platform note: Path(filename).name only strips '/'-style separators.
    On POSIX (this app's deployment target -- see Dockerfile, python:3.12-slim)
    a backslash is just a regular filename character, not a separator, so a
    payload like '..\\..\\etc\\passwd' is stored as one oddly-named literal
    file rather than escaping the directory -- verified in
    tests/test_gap_fill.py. This function would NOT be safe against
    backslash-style traversal on Windows; it isn't a deployment target here.
    """
    base = Path(get_settings().upload_dir).resolve()
    safe_name = Path(filename).name  # strips any '/'-style directory components
    project_dir = (base / str(project_id)).resolve()
    if base not in project_dir.parents and project_dir != base:
        raise HTTPException(400, "Invalid project path")
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir / f"{uuid.uuid4().hex[:8]}_{safe_name}"


@app.post("/api/projects/{project_id}/upload", dependencies=[Depends(require_api_key)])
async def upload_asset(project_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Accepts a user-supplied file (photo, unboxing video, screenshot,
    benchmark export, etc. -- spec sections 9 and 34) and stores it as a
    first-party Asset row with is_user_upload=True. Stored on local disk by
    default -- see config.py's upload_dir docstring for the production caveat."""
    project = db.get(m.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(400, f"File type {ext or '(none)'} not allowed. Allowed: {sorted(ALLOWED_UPLOAD_EXTENSIONS)}")

    settings_ = get_settings()
    max_bytes = settings_.max_upload_size_mb * 1024 * 1024
    dest = _safe_upload_path(project_id, file.filename or "upload")

    size = 0
    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"File exceeds {settings_.max_upload_size_mb}MB limit")
            f.write(chunk)

    asset = m.Asset(
        project_id=project_id,
        asset_type="upload",
        url_or_path=str(dest),
        is_user_upload=True,
        original_filename=file.filename,
        uploaded_at=utcnow(),
        copyright_status=m.CopyrightRisk.LOW,  # first-party user content — not a third-party copyright risk
        copyright_notes="User-uploaded first-party material.",
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    logger.info("Uploaded asset id=%s project=%s filename=%r size=%dB", asset.id, project_id, file.filename, size)
    return {
        "id": asset.id, "project_id": project_id, "original_filename": asset.original_filename,
        "size_bytes": size, "asset_type": asset.asset_type,
    }


@app.get("/api/projects/{project_id}/assets", dependencies=[Depends(require_api_key)])
def list_assets(project_id: int, db: Session = Depends(get_db)):
    assets = db.query(m.Asset).filter(m.Asset.project_id == project_id).all()
    return [
        {
            "id": a.id, "asset_type": a.asset_type, "url_or_path": a.url_or_path,
            "copyright_status": a.copyright_status.value if a.copyright_status else None,
            "copyright_notes": a.copyright_notes, "timestamp_in_video": a.timestamp_in_video,
            "is_user_upload": a.is_user_upload, "original_filename": a.original_filename,
        }
        for a in assets
    ]


# ---------------------------------------------------------------------------
# Unboxing / Review first-party data entry (spec section 34)
# ---------------------------------------------------------------------------

# The specific observation fields spec section 34 calls out. Kept as a plain
# list (rather than a rigid Pydantic model) so the form can hold any subset
# of these -- a reviewer rarely fills in every field.
UNBOXING_OBSERVATION_FIELDS = [
    "box_condition", "included_accessories", "build_quality", "weight", "display",
    "camera", "battery", "performance", "software", "features", "first_impressions",
    "problems", "positive_points", "negative_points",
]


@app.put("/api/projects/{project_id}/observations", dependencies=[Depends(require_api_key)])
def set_observations(project_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)):
    """Stores the reviewer's first-party, hands-on-the-product observations
    (spec section 34) into product_info under 'user_observations'. These are
    what the Script agent is instructed to treat as 'Observed' rather than
    'Expected/specification' for UNBOXING content (see script.py)."""
    project = db.get(m.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    unknown_fields = set(payload.keys()) - set(UNBOXING_OBSERVATION_FIELDS)
    if unknown_fields:
        raise HTTPException(400, f"Unknown observation fields: {sorted(unknown_fields)}. "
                                  f"Allowed: {UNBOXING_OBSERVATION_FIELDS}")

    product_info = dict(project.product_info or {})
    product_info["user_observations"] = payload
    product_info["has_first_party_observations"] = True
    project.product_info = product_info
    db.commit()
    db.refresh(project)
    return {"project_id": project_id, "user_observations": payload}


@app.get("/api/projects/{project_id}/observations", dependencies=[Depends(require_api_key)])
def get_observations(project_id: int, db: Session = Depends(get_db)):
    project = db.get(m.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return (project.product_info or {}).get("user_observations", {})


# ---------------------------------------------------------------------------
# Product Comparison Engine (spec section 35)
# ---------------------------------------------------------------------------

@app.post("/api/compare", dependencies=[Depends(require_api_key)])
def compare_projects(project_ids: List[int], db: Session = Depends(get_db)):
    """Builds a structured spec-by-spec comparison across 2+ projects, using
    only verified specification data each project's Research stage produced
    (spec section 35: 'Only use verified specifications')."""
    if len(project_ids) < 2:
        raise HTTPException(400, "Provide at least 2 project_ids to compare")

    projects = []
    for pid in project_ids:
        project = db.get(m.Project, pid)
        if not project:
            raise HTTPException(404, f"Project {pid} not found")
        research_run = pl._get_current_stage_run(db, pid, m.Stage.RESEARCH)
        projects.append({
            "id": pid, "title": project.title, "topic": project.topic,
            "specifications": (research_run.output.get("specifications") if research_run else {}) or {},
        })

    from .agents.comparison import ComparisonAgent
    agent = ComparisonAgent()
    result = agent.run(projects)
    return result


# ---------------------------------------------------------------------------
# Automatic Research Refresh (spec section 33)
# ---------------------------------------------------------------------------

@app.post("/api/projects/{project_id}/refresh-research", response_model=ProjectDetailOut, dependencies=[Depends(require_api_key)])
def refresh_research(project_id: int, max_age_hours: int = 24, db: Session = Depends(get_db)):
    """If the project's current Research (and downstream Fact Check) is
    older than max_age_hours, re-runs Research -> Fact Check to pick up any
    changes (price, availability, corrections, newly-confirmed rumors --
    spec section 33) before the project proceeds toward publish. If the
    research is still fresh, this is a no-op and returns the project as-is."""
    project = db.get(m.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    research_run = pl._get_current_stage_run(db, project_id, m.Stage.RESEARCH)
    if not research_run:
        raise HTTPException(400, "No Research stage has run yet — nothing to refresh")

    age = utcnow() - _as_aware(research_run.created_at)
    if age < dt.timedelta(hours=max_age_hours):
        logger.info("Research for project=%s is %s old, under %sh threshold — no refresh needed",
                    project_id, age, max_age_hours)
        db.refresh(project)
        return project

    logger.info("Research for project=%s is %s old, exceeds %sh threshold — refreshing", project_id, age, max_age_hours)
    project.current_stage = m.Stage.RESEARCH
    project.pipeline_state = m.PipelineState.IN_PROGRESS
    pl.run_stage(db, project, m.Stage.RESEARCH)
    if project.pipeline_state != m.PipelineState.BLOCKED:
        pl.advance_if_ready(db, project)
        if project.pipeline_state not in (m.PipelineState.BLOCKED, m.PipelineState.NEEDS_REVIEW):
            pl.run_stage(db, project, m.Stage.FACT_CHECK)
    db.commit()
    db.refresh(project)
    return project


def _as_aware(value: dt.datetime) -> dt.datetime:
    """StageRun.created_at may come back naive from SQLite (which doesn't
    preserve tzinfo) even though it was written as UTC-aware -- normalize
    before subtracting from an aware utcnow() to avoid a naive/aware
    TypeError. Postgres round-trips tzinfo correctly, so this is a SQLite-only
    compatibility shim."""
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value


# ---------------------------------------------------------------------------
# Topic Discovery / Opportunity Board (spec section 6)
# ---------------------------------------------------------------------------

@app.post("/api/idea-vault/discover", dependencies=[Depends(require_api_key)])
def discover_topics(category: str = "general technology", count: int = 5, db: Session = Depends(get_db)):
    """Runs the Discovery agent to propose new topic ideas, ranked by
    opportunity score, and adds them directly to the Idea Vault as NEW
    entries (spec section 6's Topic Opportunity Board)."""
    from .agents.discovery import DiscoveryAgent
    agent = DiscoveryAgent()
    ideas = agent.run(category=category, count=count)

    created = []
    for idea in ideas:
        entry = m.IdeaVaultEntry(
            topic=idea.get("topic", "Untitled"),
            category=category,
            content_type=_safe_content_type(idea.get("recommended_content_type")),
            opportunity_score=float(idea.get("opportunity_score", 0)),
            suggested_title=idea.get("suggested_title"),
            status="NEW",
        )
        db.add(entry)
        created.append(entry)
    db.commit()
    for e in created:
        db.refresh(e)
    return [
        {"id": e.id, "topic": e.topic, "opportunity_score": e.opportunity_score,
         "content_type": e.content_type.value if e.content_type else None, "status": e.status}
        for e in created
    ]


def _safe_content_type(value):
    try:
        return m.ContentType(value)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Final Video Package (spec section 31) — assembles every stage's output,
# the Quality Gate, sources, claims, and assets into one document per project.
# ---------------------------------------------------------------------------

@app.get("/api/projects/{project_id}/final-package", dependencies=[Depends(require_api_key)])
def get_final_package(project_id: int, db: Session = Depends(get_db)):
    project = db.get(m.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    def _current_output(stage: m.Stage) -> Dict[str, Any]:
        run = pl._get_current_stage_run(db, project_id, stage)
        return run.output if run else {}

    av = _current_output(m.Stage.AV)
    research = _current_output(m.Stage.RESEARCH)
    fact_check = _current_output(m.Stage.FACT_CHECK)
    script = _current_output(m.Stage.SCRIPT)
    voice = _current_output(m.Stage.VOICE)
    visuals = _current_output(m.Stage.VISUALS)
    copyright_ = _current_output(m.Stage.COPYRIGHT)
    thumbnail = _current_output(m.Stage.THUMBNAIL)
    publish = _current_output(m.Stage.PUBLISH)

    claims = db.query(m.Claim).filter(m.Claim.project_id == project_id).all()
    sources = db.query(m.Source).filter(m.Source.project_id == project_id).all()
    assets = db.query(m.Asset).filter(m.Asset.project_id == project_id).all()
    quality_gate = (
        db.query(m.QualityGateResult)
        .filter(m.QualityGateResult.project_id == project_id)
        .order_by(m.QualityGateResult.created_at.desc())
        .first()
    )

    return {
        "story": {
            "topic": project.topic,
            "angle": av.get("main_angle"),
            "viewer_promise": av.get("viewer_promise"),
            "alternative_angles": av.get("alternative_angles", []),
        },
        "research": {
            "facts": research.get("facts", []),
            "specifications": research.get("specifications", {}),
            "historical_context": research.get("historical_context"),
            "sources": [
                {"id": s.id, "url": s.url, "title": s.title, "publisher": s.publisher,
                 "source_tier": s.source_tier.value if s.source_tier else None}
                for s in sources
            ],
        },
        "fact_check": {
            "fact_check_score": fact_check.get("fact_check_score"),
            "claims": [
                {"id": c.id, "text": c.text,
                 "verification_status": c.verification_status.value if c.verification_status else None,
                 "confidence": c.confidence}
                for c in claims
            ],
        },
        "product_details": (project.product_info or {}) if project.content_type in (
            m.ContentType.PRODUCT_REVIEW, m.ContentType.PRODUCT_FEATURES,
            m.ContentType.PRODUCT_COMPARISON, m.ContentType.BUYING_GUIDE, m.ContentType.UNBOXING,
        ) else None,
        "script": script,
        "voice": voice,
        "visuals": visuals,
        "copyright": {
            **copyright_,
            "assets": [
                {"id": a.id, "asset_type": a.asset_type, "url_or_path": a.url_or_path,
                 "copyright_status": a.copyright_status.value if a.copyright_status else None,
                 "copyright_notes": a.copyright_notes, "is_user_upload": a.is_user_upload}
                for a in assets
            ],
        },
        "thumbnail": thumbnail,
        "publish": publish,
        "repurposing": {"shorts_ideas": publish.get("shorts_ideas", [])},
        "quality_gate": (
            {
                "overall_score": quality_gate.overall_score, "verdict": quality_gate.verdict,
                "categories": {
                    k: getattr(quality_gate, k) for k in (
                        "newsworthiness", "accuracy", "research", "originality", "script",
                        "voice", "visuals", "copyright", "thumbnail", "title", "viewer_value", "freshness",
                    )
                },
            } if quality_gate else None
        ),
        "pipeline_state": project.pipeline_state.value,
        "current_stage": project.current_stage.value,
    }


# ---------------------------------------------------------------------------
# Auth (Phase 1 foundation)
# ---------------------------------------------------------------------------

@app.post("/api/auth/register", response_model=UserOut)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(m.User).filter(m.User.email == payload.email).first()
    if existing:
        raise HTTPException(400, "An account with this email already exists")
    user = m.User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        display_name=payload.display_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("Registered user id=%s email=%s", user.id, user.email)
    return user


@app.post("/api/auth/login", response_model=TokenOut)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    user = db.query(m.User).filter(m.User.email == payload.email).first()
    # Deliberately identical error for "no such user" and "wrong password" --
    # distinguishing them lets an attacker enumerate registered emails.
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(401, "Incorrect email or password")
    if not user.is_active:
        raise HTTPException(403, "Account is disabled")
    return TokenOut(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@app.post("/api/auth/refresh", response_model=TokenOut)
def refresh(payload: RefreshIn, db: Session = Depends(get_db)):
    try:
        user_id = decode_token(payload.refresh_token, expected_type="refresh")
    except InvalidTokenError as exc:
        raise HTTPException(401, f"Invalid refresh token: {exc}")
    user = db.get(m.User, user_id)
    if not user or not user.is_active:
        raise HTTPException(401, "User not found or inactive")
    return TokenOut(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),  # rotate -- see note below
    )


@app.get("/api/auth/me", response_model=UserOut)
def get_me(current_user: m.User = Depends(get_current_user)):
    return current_user