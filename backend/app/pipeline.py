"""Pipeline orchestrator — see ARCHITECTURE.md section 3 for the full design
rationale. This module is the only place that is allowed to create StageRun
rows or move a Project's current_stage/pipeline_state.
"""
from __future__ import annotations
import datetime as dt
from typing import Any, Dict

from sqlalchemy.orm import Session

from . import models as m
from .models import utcnow
from .config import get_settings
from .agents.av import AVAgent
from .agents.research import ResearchAgent
from .agents.fact_check import FactCheckAgent
from .agents.script import ScriptAgent
from .agents.voice import VoiceAgent
from .agents.visuals import VisualsAgent
from .agents.copyright import CopyrightAgent
from .agents.thumbnail import ThumbnailAgent
from .agents.publish import PublishAgent
from .agents.quality_gate import QualityGateAgent

AGENTS = {
    m.Stage.AV: AVAgent,
    m.Stage.RESEARCH: ResearchAgent,
    m.Stage.FACT_CHECK: FactCheckAgent,
    m.Stage.SCRIPT: ScriptAgent,
    m.Stage.VOICE: VoiceAgent,
    m.Stage.VISUALS: VisualsAgent,
    m.Stage.COPYRIGHT: CopyrightAgent,
    m.Stage.THUMBNAIL: ThumbnailAgent,
    m.Stage.PUBLISH: PublishAgent,
}


def _get_current_stage_run(db: Session, project_id: int, stage: m.Stage) -> m.StageRun | None:
    """Direct query, deliberately NOT going through project.stage_runs.

    Within a single request we run several stages back-to-back on the same
    Project instance/session. SQLAlchemy caches a relationship collection on
    first access, so project.stage_runs (and the current_stage_run() helper
    built on it) can go stale mid-request as new StageRun rows are added by
    earlier iterations of the same loop. A direct query is always correct.
    """
    return (
        db.query(m.StageRun)
        .filter(
            m.StageRun.project_id == project_id,
            m.StageRun.stage == stage,
            m.StageRun.is_current == True,  # noqa: E712
        )
        .order_by(m.StageRun.version_number.desc())
        .first()
    )


def _build_context(db: Session, project: m.Project) -> Dict[str, Any]:
    """Every upstream stage's *current* output, keyed by stage name."""
    ctx = {}
    for stage in m.STAGE_ORDER:
        run = _get_current_stage_run(db, project.id, stage)
        if run is not None:
            ctx[stage.value] = run.output
    return ctx


def _next_version_number(db: Session, project_id: int, stage: m.Stage) -> int:
    existing = (
        db.query(m.StageRun)
        .filter(m.StageRun.project_id == project_id, m.StageRun.stage == stage)
        .count()
    )
    return existing + 1


def run_stage(db: Session, project: m.Project, stage: m.Stage) -> m.StageRun:
    """Run the agent for `stage`, persist a new StageRun version, and update
    the project's pipeline_state according to that stage's gate. Does NOT
    advance current_stage on its own in REVIEW mode — see advance_if_ready()."""
    agent_cls = AGENTS[stage]
    agent = agent_cls()
    context = _build_context(db, project)

    input_snapshot = {
        "project": {
            "topic": project.topic,
            "content_type": project.content_type.value,
            "duration_target_seconds": project.duration_target_seconds,
        },
        "upstream_context": context,
    }

    # Flip any existing current run for this stage to non-current (keep history).
    for run in db.query(m.StageRun).filter(
        m.StageRun.project_id == project.id, m.StageRun.stage == stage, m.StageRun.is_current == True  # noqa: E712
    ):
        run.is_current = False

    version = _next_version_number(db, project.id, stage)
    stage_run = m.StageRun(
        project_id=project.id,
        stage=stage,
        version_number=version,
        status=m.StageRunStatus.RUNNING,
        input_snapshot=input_snapshot,
        output={},
        is_current=True,
    )
    db.add(stage_run)
    db.flush()

    try:
        output = agent.run(project, context)
    except Exception as exc:  # pragma: no cover - defensive
        stage_run.status = m.StageRunStatus.FAILED
        stage_run.output = {"error": str(exc)}
        project.pipeline_state = m.PipelineState.BLOCKED
        db.flush()
        return stage_run

    stage_run.output = dict(output)
    stage_run.status = m.StageRunStatus.SUCCEEDED

    # Stage-specific side effects: persist Sources/Claims/Assets, compute gate scores.
    if stage == m.Stage.RESEARCH:
        _persist_research_side_effects(db, project, stage_run)
    elif stage == m.Stage.FACT_CHECK:
        score = _persist_fact_check_side_effects(db, project, stage_run)
        stage_run.score = score
    elif stage == m.Stage.VISUALS:
        _persist_visuals_side_effects(db, project, stage_run)
    elif stage == m.Stage.COPYRIGHT:
        _persist_copyright_side_effects(db, project, stage_run)

    db.flush()
    _apply_gate(db, project, stage, stage_run)
    project.updated_at = utcnow()
    db.flush()
    return stage_run


def _persist_research_side_effects(db: Session, project: m.Project, stage_run: m.StageRun) -> None:
    output = stage_run.output
    for src in output.get("sources", []):
        db.add(m.Source(
            project_id=project.id,
            url=src.get("url", ""),
            title=src.get("title"),
            publisher=src.get("publisher"),
            source_tier=_safe_enum(m.SourceTier, src.get("source_tier"), m.SourceTier.REPUTABLE_PRESS),
        ))
    for fact in output.get("facts", []):
        db.add(m.Claim(
            project_id=project.id,
            stage_run_id=stage_run.id,
            text=fact.get("text", ""),
            claim_type=fact.get("claim_type", "other"),
            verification_status=m.VerificationStatus.REPORTED,
            confidence=0.0,
            source_ids=[],
        ))


def _persist_fact_check_side_effects(db: Session, project: m.Project, stage_run: m.StageRun) -> float:
    output = stage_run.output
    # Update the most recent Claim rows for this project with verification results.
    # Matched positionally against the Research stage's claims for this scaffold;
    # a production system would match by claim id once agents return stable ids.
    research_run = _get_current_stage_run(db, project.id, m.Stage.RESEARCH)
    claims = (
        db.query(m.Claim)
        .filter(m.Claim.project_id == project.id, m.Claim.stage_run_id == (research_run.id if research_run else -1))
        .all()
    )
    fc_claims = output.get("claims", [])
    for claim_row, fc_claim in zip(claims, fc_claims):
        claim_row.verification_status = _safe_enum(
            m.VerificationStatus, fc_claim.get("verification_status"), m.VerificationStatus.REPORTED
        )
        claim_row.confidence = float(fc_claim.get("confidence", 0))
        claim_row.fact_checked_at = utcnow()

    return float(output.get("fact_check_score", 0))


def _persist_visuals_side_effects(db: Session, project: m.Project, stage_run: m.StageRun) -> None:
    """Persists the Visuals agent's storyboard as real Asset rows (spec
    section 39's asset library) — one per storyboard shot, carrying its own
    first-pass copyright estimate until the Copyright stage's independent
    review updates it."""
    for shot in stage_run.output.get("storyboard", []):
        db.add(m.Asset(
            project_id=project.id,
            stage_run_id=stage_run.id,
            asset_type=shot.get("asset_type", "graphic"),
            url_or_path=shot.get("source") or shot.get("visual", ""),
            copyright_status=_safe_enum(m.CopyrightRisk, shot.get("copyright_status"), m.CopyrightRisk.LOW),
            copyright_notes="First-pass estimate from the Visuals agent — not yet independently reviewed.",
            timestamp_in_video=shot.get("timestamp"),
        ))


def _persist_copyright_side_effects(db: Session, project: m.Project, stage_run: m.StageRun) -> None:
    """Updates the Asset rows created by the Visuals stage with the
    Copyright agent's independent review — matched positionally in the same
    way _persist_fact_check_side_effects matches claims (see that function's
    docstring for the caveat: a production system would match by a stable
    asset id once agents return one, rather than by list order)."""
    visuals_run = _get_current_stage_run(db, project.id, m.Stage.VISUALS)
    assets = (
        db.query(m.Asset)
        .filter(m.Asset.project_id == project.id, m.Asset.stage_run_id == (visuals_run.id if visuals_run else -1))
        .order_by(m.Asset.id)
        .all()
    )
    reviews = stage_run.output.get("asset_reviews", [])
    for asset_row, review in zip(assets, reviews):
        asset_row.copyright_status = _safe_enum(m.CopyrightRisk, review.get("risk"), asset_row.copyright_status)
        asset_row.copyright_notes = review.get("reason", asset_row.copyright_notes)


def _safe_enum(enum_cls, value, default):
    try:
        return enum_cls(value)
    except Exception:
        return default


def _apply_gate(db: Session, project: m.Project, stage: m.Stage, stage_run: m.StageRun) -> None:
    """Section 3 of ARCHITECTURE.md: decide whether the project can advance."""
    if stage == m.Stage.FACT_CHECK:
        threshold = get_settings().fact_check_threshold
        if (stage_run.score or 0) < threshold:
            project.pipeline_state = m.PipelineState.BLOCKED
            project.current_stage = m.Stage.RESEARCH  # loop back per spec section 11
            return

    if project.pipeline_mode == m.PipelineMode.REVIEW and stage in m.REVIEW_GATE_STAGES:
        project.pipeline_state = m.PipelineState.NEEDS_REVIEW
        return

    project.pipeline_state = m.PipelineState.IN_PROGRESS


def advance_if_ready(db: Session, project: m.Project) -> None:
    """Move current_stage to the next stage in STAGE_ORDER if the current
    stage's gate allows it. Called after an approval, or automatically in
    AUTO mode right after run_stage().

    Only BLOCKED (a genuine gate failure, e.g. a low fact-check score) halts
    this. NEEDS_REVIEW is deliberately NOT a guard here: that state means
    "waiting for human approval", and approve_current_stage() calls this
    function precisely at the moment that approval has just been granted --
    guarding on NEEDS_REVIEW would make approval a silent no-op. In AUTO mode
    (the only mode where this is called automatically right after run_stage),
    NEEDS_REVIEW is never set in the first place, so this relaxation doesn't
    change AUTO-mode behavior at all.

    Advancing past the last stage (PUBLISH) triggers the Final Quality Gate
    (spec section 30) instead of unconditionally marking READY_TO_PUBLISH --
    the gate's verdict is what actually decides the resulting pipeline_state.
    """
    if project.pipeline_state == m.PipelineState.BLOCKED:
        return
    idx = m.STAGE_ORDER.index(project.current_stage)
    if idx < len(m.STAGE_ORDER) - 1:
        project.current_stage = m.STAGE_ORDER[idx + 1]
        project.pipeline_state = m.PipelineState.NOT_STARTED
        db.flush()
    else:
        compute_quality_gate(db, project)


# Categories where a near-zero score is a hard fail regardless of the overall
# sum -- spec section 30: "never let a high overall score paper over an
# accuracy or copyright failure". Enforced here in code, not left to the LLM
# to remember to apply consistently every time.
HARD_FAIL_CATEGORIES = ("accuracy", "copyright")
HARD_FAIL_THRESHOLD = 3.0


def compute_quality_gate(db: Session, project: m.Project, agent: QualityGateAgent | None = None) -> m.QualityGateResult:
    """Runs the Quality Gate agent over everything the pipeline produced and
    persists a QualityGateResult row. Sets pipeline_state from the verdict:
      READY_TO_PUBLISH   -> PipelineState.READY_TO_PUBLISH
      NEEDS_REVIEW        -> PipelineState.NEEDS_REVIEW
      RESEARCH_REQUIRED   -> PipelineState.BLOCKED, current_stage looped back to RESEARCH
      DO_NOT_PUBLISH       -> PipelineState.DO_NOT_PUBLISH
    """
    agent = agent or QualityGateAgent()
    context = _build_context(db, project)
    publish_run = _get_current_stage_run(db, project.id, m.Stage.PUBLISH)
    output = agent.run(project, context)

    categories = {k: float(output.get(k, 0)) for k in (
        "newsworthiness", "accuracy", "research", "originality", "script", "voice",
        "visuals", "copyright", "thumbnail", "title", "viewer_value", "freshness",
    )}
    overall_score = float(output.get("overall_score", sum(categories.values())))
    verdict = output.get("verdict", "NEEDS_REVIEW")

    hard_fail = any(categories[c] < HARD_FAIL_THRESHOLD for c in HARD_FAIL_CATEGORIES)
    if hard_fail:
        verdict = "DO_NOT_PUBLISH"

    result = m.QualityGateResult(
        project_id=project.id,
        stage_run_id=publish_run.id if publish_run else None,
        overall_score=overall_score,
        verdict=verdict,
        **categories,
    )
    db.add(result)

    if verdict == "READY_TO_PUBLISH":
        project.pipeline_state = m.PipelineState.READY_TO_PUBLISH
    elif verdict == "RESEARCH_REQUIRED":
        project.pipeline_state = m.PipelineState.BLOCKED
        project.current_stage = m.Stage.RESEARCH
    elif verdict == "DO_NOT_PUBLISH":
        project.pipeline_state = m.PipelineState.DO_NOT_PUBLISH
    else:
        project.pipeline_state = m.PipelineState.NEEDS_REVIEW

    project.overall_quality_score = overall_score
    db.flush()
    return result


def approve_current_stage(db: Session, project: m.Project, note: str | None, actor: str) -> None:
    db.add(m.ApprovalEvent(
        project_id=project.id, stage=project.current_stage,
        action=m.ApprovalAction.APPROVE, actor=actor, note=note,
    ))
    advance_if_ready(db, project)
    db.flush()


def run_full_pipeline_auto(db: Session, project: m.Project) -> None:
    """Runs every remaining stage from the project's current_stage through
    PUBLISH (and then the Quality Gate), auto-advancing as long as gates
    pass. Stops at whatever stage first needs review or gets blocked --
    this is what the AUTO pipeline_mode is for; REVIEW-mode projects should
    be driven stage-by-stage via run_stage()/approve_current_stage() instead."""
    for _ in range(len(m.STAGE_ORDER)):
        stage = project.current_stage
        run_stage(db, project, stage)
        if project.pipeline_state in (m.PipelineState.BLOCKED, m.PipelineState.NEEDS_REVIEW):
            return
        advance_if_ready(db, project)
        if project.pipeline_state in (
            m.PipelineState.READY_TO_PUBLISH, m.PipelineState.DO_NOT_PUBLISH,
            m.PipelineState.BLOCKED, m.PipelineState.NEEDS_REVIEW,
        ):
            return


# Backwards-compatible alias for the original 3-stage vertical slice, kept
# because main.py's /run-vertical-slice endpoint is documented as exactly
# that slice; use run_full_pipeline_auto for the full 9-stage run.
def run_full_slice_auto(db: Session, project: m.Project) -> None:
    for _ in range(3):  # AV, RESEARCH, FACT_CHECK
        stage = project.current_stage
        if stage not in (m.Stage.AV, m.Stage.RESEARCH, m.Stage.FACT_CHECK):
            break
        run_stage(db, project, stage)
        if project.pipeline_state in (m.PipelineState.BLOCKED, m.PipelineState.NEEDS_REVIEW):
            break
        advance_if_ready(db, project)
