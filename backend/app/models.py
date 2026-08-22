"""Core data model.

Every important design decision here is explained in ARCHITECTURE.md section 2.
Key invariant enforced at the ORM layer: StageRun rows are never deleted or
mutated after creation (see pipeline.py) — only `is_current` flips.
"""
import enum
import datetime as dt

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, ForeignKey, JSON, Enum, Text
)
from sqlalchemy.orm import relationship

from .database import Base
from sqlalchemy.types import TypeDecorator, Text as _Text
from .crypto import encrypt_secret, decrypt_secret


def utcnow():
    # Timezone-aware — datetime.utcnow() is deprecated as of Python 3.12.
    return dt.datetime.now(dt.timezone.utc)


class ContentType(str, enum.Enum):
    NEWS = "NEWS"
    BREAKING_NEWS = "BREAKING_NEWS"
    LONG_FORM = "LONG_FORM"
    SHORT = "SHORT"
    PRODUCT_REVIEW = "PRODUCT_REVIEW"
    UNBOXING = "UNBOXING"
    PRODUCT_FEATURES = "PRODUCT_FEATURES"
    PRODUCT_COMPARISON = "PRODUCT_COMPARISON"
    BUYING_GUIDE = "BUYING_GUIDE"
    EXPLAINER = "EXPLAINER"
    RUMOR_LEAK = "RUMOR_LEAK"
    SOFTWARE_OS = "SOFTWARE_OS"
    AI_NEWS = "AI_NEWS"


class Priority(str, enum.Enum):
    BREAKING = "BREAKING"
    HIGH_DEMAND = "HIGH_DEMAND"
    PRODUCT_LAUNCH = "PRODUCT_LAUNCH"
    TRENDING = "TRENDING"
    EVERGREEN = "EVERGREEN"
    LOW_PRIORITY = "LOW_PRIORITY"


class PipelineMode(str, enum.Enum):
    AUTO = "AUTO"
    REVIEW = "REVIEW"


class Stage(str, enum.Enum):
    AV = "AV"
    RESEARCH = "RESEARCH"
    FACT_CHECK = "FACT_CHECK"
    SCRIPT = "SCRIPT"
    VOICE = "VOICE"
    VISUALS = "VISUALS"
    COPYRIGHT = "COPYRIGHT"
    THUMBNAIL = "THUMBNAIL"
    PUBLISH = "PUBLISH"


STAGE_ORDER = [
    Stage.AV, Stage.RESEARCH, Stage.FACT_CHECK, Stage.SCRIPT, Stage.VOICE,
    Stage.VISUALS, Stage.COPYRIGHT, Stage.THUMBNAIL, Stage.PUBLISH,
]

# Stages where REVIEW mode always pauses for human approval regardless of score
REVIEW_GATE_STAGES = {Stage.RESEARCH, Stage.FACT_CHECK, Stage.SCRIPT, Stage.COPYRIGHT}


class PipelineState(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCKED = "BLOCKED"
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    PUBLISHED = "PUBLISHED"
    DO_NOT_PUBLISH = "DO_NOT_PUBLISH"
    REJECTED = "REJECTED"


class StageRunStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class VerificationStatus(str, enum.Enum):
    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    REPORTED = "REPORTED"
    RUMOR = "RUMOR"
    CONTRADICTED = "CONTRADICTED"
    UNSUPPORTED = "UNSUPPORTED"
    FALSE = "FALSE"


class SourceTier(str, enum.Enum):
    OFFICIAL = "OFFICIAL"
    COMPANY = "COMPANY"
    PRODUCT_PAGE = "PRODUCT_PAGE"
    REGULATORY = "REGULATORY"
    DEV_DOCS = "DEV_DOCS"
    REPUTABLE_PRESS = "REPUTABLE_PRESS"
    SPECIALIST_PRESS = "SPECIALIST_PRESS"
    COMMUNITY = "COMMUNITY"


class CopyrightRisk(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ApprovalAction(str, enum.Enum):
    APPROVE = "APPROVE"
    EDIT = "EDIT"
    REGENERATE = "REGENERATE"
    SEND_BACK = "SEND_BACK"
    REJECT = "REJECT"

class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
class EncryptedText(TypeDecorator):
    """Transparently encrypts on write, decrypts on read. Column stays TEXT
    in the actual schema -- Fernet ciphertext is ASCII-safe base64, so no
    migration is needed on the column type itself, only on re-saving
    existing plaintext rows once this is deployed (see the note in the
    YouTubeConfig migration below)."""
    impl = _Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_secret(value)

    def process_result_value(self, value, dialect):
        return decrypt_secret(value)

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False, unique=True, index=True)
    hashed_password = Column(String, nullable=False)
    display_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)

    projects = relationship("Project", back_populates="owner")
class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # nullable for now -- see migration note

    owner = relationship("User", back_populates="projects")
    title = Column(String, nullable=False)
    topic = Column(String, nullable=False)
    content_type = Column(Enum(ContentType), nullable=False)

    duration_target_seconds = Column(Integer, nullable=True)
    target_audience = Column(String, nullable=True)
    tone = Column(String, nullable=True)
    language = Column(String, default="en")

    user_instructions = Column(Text, nullable=True)
    source_urls = Column(JSON, default=list)
    product_info = Column(JSON, default=dict)

    priority = Column(Enum(Priority), default=Priority.EVERGREEN)
    pipeline_mode = Column(Enum(PipelineMode), default=PipelineMode.REVIEW)

    current_stage = Column(Enum(Stage), default=Stage.AV)
    pipeline_state = Column(Enum(PipelineState), default=PipelineState.NOT_STARTED)

    freshness_score = Column(Float, nullable=True)
    overall_quality_score = Column(Float, nullable=True)

    scheduled_publish_at = Column(DateTime, nullable=True)
    published_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    stage_runs = relationship("StageRun", back_populates="project", cascade="all, delete-orphan")
    sources = relationship("Source", back_populates="project", cascade="all, delete-orphan")
    claims = relationship("Claim", back_populates="project", cascade="all, delete-orphan")
    assets = relationship("Asset", back_populates="project", cascade="all, delete-orphan")
    approvals = relationship("ApprovalEvent", back_populates="project", cascade="all, delete-orphan")
    quality_gates = relationship("QualityGateResult", back_populates="project", cascade="all, delete-orphan")

    def current_stage_run(self, stage: "Stage"):
        """Convenience only for a freshly-loaded Project in a short-lived
        session/request. Anything in pipeline.py that runs multiple stages
        back-to-back on the same session uses pipeline._get_current_stage_run()
        (a direct query) instead, since this relationship-based lookup can go
        stale mid-request — see that function's docstring."""
        candidates = [r for r in self.stage_runs if r.stage == stage and r.is_current]
        return candidates[0] if candidates else None


class StageRun(Base):
    __tablename__ = "stage_runs"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    stage = Column(Enum(Stage), nullable=False)
    version_number = Column(Integer, nullable=False)
    status = Column(Enum(StageRunStatus), default=StageRunStatus.PENDING)

    input_snapshot = Column(JSON, default=dict)
    output = Column(JSON, default=dict)
    score = Column(Float, nullable=True)
    is_current = Column(Boolean, default=True)

    created_at = Column(DateTime, default=utcnow)

    project = relationship("Project", back_populates="stage_runs")


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    url = Column(String, nullable=False)
    title = Column(String, nullable=True)
    publisher = Column(String, nullable=True)
    source_tier = Column(Enum(SourceTier), default=SourceTier.REPUTABLE_PRESS)
    retrieved_at = Column(DateTime, default=utcnow)
    notes = Column(Text, nullable=True)

    project = relationship("Project", back_populates="sources")


class Claim(Base):
    __tablename__ = "claims"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    stage_run_id = Column(Integer, ForeignKey("stage_runs.id"), nullable=False)
    text = Column(Text, nullable=False)
    claim_type = Column(String, default="other")
    verification_status = Column(Enum(VerificationStatus), default=VerificationStatus.REPORTED)
    confidence = Column(Float, default=0.0)
    source_ids = Column(JSON, default=list)
    fact_checked_at = Column(DateTime, nullable=True)

    project = relationship("Project", back_populates="claims")


class Asset(Base):
    __tablename__ = "assets"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    stage_run_id = Column(Integer, ForeignKey("stage_runs.id"), nullable=True)
    asset_type = Column(String, nullable=False)
    url_or_path = Column(String, nullable=True)
    copyright_status = Column(Enum(CopyrightRisk), default=CopyrightRisk.LOW)
    copyright_notes = Column(Text, nullable=True)
    timestamp_in_video = Column(String, nullable=True)

    # User-uploaded first-party material (spec sections 9, 34) is
    # distinguished from agent-described storyboard assets by this flag --
    # an upload is a real file on disk; a Visuals-agent asset row is a
    # description of a shot that doesn't necessarily exist as a file yet.
    is_user_upload = Column(Boolean, default=False)
    original_filename = Column(String, nullable=True)
    uploaded_at = Column(DateTime, nullable=True)

    project = relationship("Project", back_populates="assets")


class QualityGateResult(Base):
    __tablename__ = "quality_gate_results"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    stage_run_id = Column(Integer, ForeignKey("stage_runs.id"), nullable=True)

    newsworthiness = Column(Float, default=0)
    accuracy = Column(Float, default=0)
    research = Column(Float, default=0)
    originality = Column(Float, default=0)
    script = Column(Float, default=0)
    voice = Column(Float, default=0)
    visuals = Column(Float, default=0)
    copyright = Column(Float, default=0)
    thumbnail = Column(Float, default=0)
    title = Column(Float, default=0)
    viewer_value = Column(Float, default=0)
    freshness = Column(Float, default=0)

    overall_score = Column(Float, default=0)
    verdict = Column(String, default="NEEDS_REVIEW")
    created_at = Column(DateTime, default=utcnow)

    project = relationship("Project", back_populates="quality_gates")


class ApprovalEvent(Base):
    __tablename__ = "approval_events"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    stage = Column(Enum(Stage), nullable=False)
    action = Column(Enum(ApprovalAction), nullable=False)
    actor = Column(String, default="user")
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    project = relationship("Project", back_populates="approvals")


class ScheduleConfig(Base):
    __tablename__ = "schedule_config"

    id = Column(Integer, primary_key=True)
    full_videos_per_day = Column(Integer, default=1)
    shorts_per_day = Column(Integer, default=24)
    shorts_interval_minutes = Column(Integer, default=30)
    publishing_window_start = Column(String, default="09:00")
    publishing_window_end = Column(String, default="21:00")
    weekday_overrides = Column(JSON, default=dict)
    weekend_overrides = Column(JSON, default=dict)
    quality_threshold = Column(Float, default=85.0)
    max_daily_output = Column(Integer, nullable=True)


class YouTubeConfig(Base):
    __tablename__ = "youtube_config"

    id = Column(Integer, primary_key=True)
    channel_id = Column(String, nullable=True)
    channel_name = Column(String, nullable=True)
    default_privacy_status = Column(String, default="private")
    default_category_id = Column(String, default="28")
    default_language = Column(String, default="en")
    default_tags = Column(JSON, default=list)
    made_for_kids = Column(Boolean, default=False)
    auto_publish_enabled = Column(Boolean, default=False)
    upload_description_footer = Column(Text, nullable=True)
    client_id = Column(EncryptedText, nullable=True)
    client_secret = Column(EncryptedText, nullable=True)
    refresh_token = Column(EncryptedText, nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class IdeaVaultEntry(Base):
    __tablename__ = "idea_vault"

    id = Column(Integer, primary_key=True)
    topic = Column(String, nullable=False)
    source = Column(String, nullable=True)
    category = Column(String, nullable=True)
    content_type = Column(Enum(ContentType), nullable=True)
    priority = Column(Enum(Priority), default=Priority.EVERGREEN)
    opportunity_score = Column(Float, default=0)
    suggested_title = Column(String, nullable=True)
    status = Column(String, default="NEW")
    created_at = Column(DateTime, default=utcnow)
    expires_at = Column(DateTime, nullable=True)


class SchedulerLock(Base):
    """A single-row DB-backed mutex the scheduler tick uses to stay correct
    across multiple backend replicas -- see scheduler.py's docstring. Any
    replica can attempt to acquire it; only one succeeds per lease period.
    This works on both SQLite and Postgres because it relies only on a
    UNIQUE constraint + a conditional UPDATE, not a database-specific
    advisory-lock feature.
    """
    __tablename__ = "scheduler_lock"

    id = Column(Integer, primary_key=True)  # always row id=1 — singleton
    holder = Column(String, nullable=True)   # opaque identifier for whichever process holds it
    acquired_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)


class JobRun(Base):
    """Durable record of an async job, independent of Celery's own result
    backend (which expires results and isn't queryable by arbitrary filters
    the way a real table is). One row per enqueued task -- the API reports
    status from this table, never from Celery directly."""
    __tablename__ = "job_runs"

    id = Column(Integer, primary_key=True)
    job_type = Column(String, nullable=False)  # e.g. "run_stage", later "synthesize_voice", "render_video"
    celery_task_id = Column(String, nullable=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    status = Column(Enum(JobStatus), default=JobStatus.PENDING)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)