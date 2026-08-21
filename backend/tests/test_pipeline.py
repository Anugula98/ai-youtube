"""Tests for pipeline.py — the state machine is the highest-value thing to
regression-test here, since it's what enforces the spec's "quality gates
override quantity targets" rule (section 21/38)."""
from app import models as m
from app import pipeline as pl


def _make_project(db, **overrides):
    defaults = dict(
        title="Test project",
        topic="Test topic",
        content_type=m.ContentType.NEWS,
        pipeline_mode=m.PipelineMode.AUTO,
        current_stage=m.Stage.AV,
        pipeline_state=m.PipelineState.NOT_STARTED,
    )
    defaults.update(overrides)
    project = m.Project(**defaults)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def test_av_stage_advances_to_research_in_auto_mode(db):
    project = _make_project(db)
    pl.run_stage(db, project, m.Stage.AV)
    pl.advance_if_ready(db, project)
    db.commit()
    assert project.current_stage == m.Stage.RESEARCH


def test_low_fact_check_score_blocks_and_loops_back_to_research(db):
    """Spec section 11: 'If below the configured threshold: STOP -> RESEARCH'.
    MockLLMClient's fact-check stub always scores 55, which is below the
    default 70 threshold, so this should reliably block."""
    project = _make_project(db, current_stage=m.Stage.FACT_CHECK)
    pl.run_stage(db, project, m.Stage.AV)
    pl.advance_if_ready(db, project)
    pl.run_stage(db, project, m.Stage.RESEARCH)
    pl.advance_if_ready(db, project)
    run = pl.run_stage(db, project, m.Stage.FACT_CHECK)
    db.commit()

    assert run.score == 55.0
    assert project.pipeline_state == m.PipelineState.BLOCKED
    assert project.current_stage == m.Stage.RESEARCH  # looped back, not advanced


def test_review_mode_pauses_after_research_regardless_of_score(db):
    """Spec section 29: REVIEW mode pauses after Research/FactCheck/Script/
    Copyright even when nothing is technically blocked."""
    project = _make_project(db, pipeline_mode=m.PipelineMode.REVIEW)
    pl.run_stage(db, project, m.Stage.AV)
    db.commit()
    assert project.pipeline_state == m.PipelineState.IN_PROGRESS  # AV isn't a review-gate stage

    project.current_stage = m.Stage.RESEARCH
    pl.run_stage(db, project, m.Stage.RESEARCH)
    db.commit()
    assert project.pipeline_state == m.PipelineState.NEEDS_REVIEW
    assert project.current_stage == m.Stage.RESEARCH  # did not auto-advance


def test_approving_a_needs_review_stage_advances_it(db):
    project = _make_project(db, pipeline_mode=m.PipelineMode.REVIEW, current_stage=m.Stage.RESEARCH)
    pl.run_stage(db, project, m.Stage.RESEARCH)
    db.commit()
    assert project.pipeline_state == m.PipelineState.NEEDS_REVIEW

    pl.approve_current_stage(db, project, note=None, actor="tester")
    db.commit()
    assert project.current_stage == m.Stage.FACT_CHECK


def test_rerunning_a_stage_preserves_history_as_new_version(db):
    """Spec section 27/28: nothing should disappear when an agent is
    regenerated — every regeneration creates a new version, old ones stay."""
    project = _make_project(db)
    pl.run_stage(db, project, m.Stage.AV)
    pl.run_stage(db, project, m.Stage.AV)  # re-run without advancing
    db.commit()

    runs = db.query(m.StageRun).filter(
        m.StageRun.project_id == project.id, m.StageRun.stage == m.Stage.AV
    ).order_by(m.StageRun.version_number).all()

    assert len(runs) == 2
    assert runs[0].version_number == 1 and runs[0].is_current is False
    assert runs[1].version_number == 2 and runs[1].is_current is True


def test_research_stage_persists_sources_and_claims(db):
    project = _make_project(db)
    pl.run_stage(db, project, m.Stage.AV)
    pl.run_stage(db, project, m.Stage.RESEARCH)
    db.commit()

    sources = db.query(m.Source).filter(m.Source.project_id == project.id).all()
    claims = db.query(m.Claim).filter(m.Claim.project_id == project.id).all()
    assert len(sources) >= 1
    assert len(claims) >= 1
    assert claims[0].verification_status == m.VerificationStatus.REPORTED  # not yet fact-checked


def test_fact_check_updates_claim_verification_and_confidence(db):
    """Regression test for a real bug caught during development: the
    fact-check stage was reading a stale cached relationship and silently
    failing to update claim rows across multiple stages in one request."""
    project = _make_project(db)
    pl.run_stage(db, project, m.Stage.AV)
    pl.run_stage(db, project, m.Stage.RESEARCH)
    pl.run_stage(db, project, m.Stage.FACT_CHECK)
    db.commit()

    claims = db.query(m.Claim).filter(m.Claim.project_id == project.id).all()
    assert len(claims) >= 1
    assert claims[0].confidence == 55.0
    assert claims[0].fact_checked_at is not None
