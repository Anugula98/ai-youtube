"""Pydantic schemas for API I/O. Kept separate from ORM models so the API
contract can evolve independently of storage (e.g. hiding internal fields)."""
from __future__ import annotations
import datetime as dt
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field

from .models import ContentType, Priority, PipelineMode, Stage, PipelineState


class ScheduleConfigIn(BaseModel):
    full_videos_per_day: int = 1
    shorts_per_day: int = 24
    shorts_interval_minutes: int = 30
    publishing_window_start: str = "09:00"
    publishing_window_end: str = "21:00"
    weekday_overrides: Dict[str, Any] = Field(default_factory=dict)
    weekend_overrides: Dict[str, Any] = Field(default_factory=dict)
    quality_threshold: float = 85.0
    max_daily_output: Optional[int] = None


class ScheduleConfigOut(ScheduleConfigIn):
    id: int

    model_config = {"from_attributes": True}


class YouTubeConfigIn(BaseModel):
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None
    default_privacy_status: str = "private"
    default_category_id: str = "28"
    default_language: str = "en"
    default_tags: List[str] = Field(default_factory=list)
    made_for_kids: bool = False
    auto_publish_enabled: bool = False
    upload_description_footer: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    refresh_token: Optional[str] = None


class YouTubeConfigOut(BaseModel):
    id: int
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None
    default_privacy_status: str
    default_category_id: str
    default_language: str
    default_tags: List[str]
    made_for_kids: bool
    auto_publish_enabled: bool
    upload_description_footer: Optional[str] = None
    has_client_id: bool
    has_client_secret: bool
    has_refresh_token: bool


class IdeaVaultEntryIn(BaseModel):
    topic: str
    source: Optional[str] = None
    category: Optional[str] = None
    content_type: Optional[ContentType] = None
    priority: Priority = Priority.EVERGREEN
    opportunity_score: float = 0
    suggested_title: Optional[str] = None
    status: str = "NEW"


class IdeaVaultEntryOut(IdeaVaultEntryIn):
    id: int
    created_at: dt.datetime
    expires_at: Optional[dt.datetime] = None

    model_config = {"from_attributes": True}


class ProjectCreate(BaseModel):
    title: str
    topic: str
    content_type: ContentType
    duration_target_seconds: Optional[int] = None
    target_audience: Optional[str] = None
    tone: Optional[str] = None
    language: str = "en"
    user_instructions: Optional[str] = None
    source_urls: List[str] = Field(default_factory=list)
    product_info: Dict[str, Any] = Field(default_factory=dict)
    priority: Priority = Priority.EVERGREEN
    pipeline_mode: PipelineMode = PipelineMode.REVIEW


class ApprovalIn(BaseModel):
    action: str  # APPROVE / EDIT / REGENERATE / SEND_BACK / REJECT
    note: Optional[str] = None
    actor: str = "user"


class StageRunOut(BaseModel):
    id: int
    stage: Stage
    version_number: int
    status: str
    output: Dict[str, Any]
    score: Optional[float]
    is_current: bool
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class ProjectOut(BaseModel):
    id: int
    title: str
    topic: str
    content_type: ContentType
    current_stage: Stage
    pipeline_state: PipelineState
    priority: Priority
    pipeline_mode: PipelineMode
    overall_quality_score: Optional[float]
    created_at: dt.datetime
    updated_at: dt.datetime

    model_config = {"from_attributes": True}


class ProjectDetailOut(ProjectOut):
    duration_target_seconds: Optional[int]
    target_audience: Optional[str]
    tone: Optional[str]
    language: str
    user_instructions: Optional[str]
    source_urls: List[str]
    product_info: Dict[str, Any]
    stage_runs: List[StageRunOut]

    model_config = {"from_attributes": True}
