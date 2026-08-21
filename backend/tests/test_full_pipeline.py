"""Tests for the full 9-stage pipeline (Script through Publish, added after
the initial 3-stage vertical slice) and the Final Quality Gate."""
from app import models as m
from app import pipeline as pl


def _make_project(db, **overrides):
    defaults = dict(
        title="Full pipeline test",
        topic="Full pipeline test topic",
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


def _clear_fact_check_gate(db, project):
    """Fact-check's mock score (55) is below the default 70 threshold, which
    correctly blocks the vertical slice — that's tested elsewhere. Tests in
    this file care about stages *after* Fact Check, so simulate "the score
    was actually fine" by raising the persisted score and undoing the
    block's loop-back-to-RESEARCH side effect before re-advancing."""
    run = pl._get_current_stage_run(db, project.id, m.Stage.FACT_CHECK)
    run.score = 90.0
    project.current_stage = m.Stage.FACT_CHECK  # undo run_stage's loop-back-to-RESEARCH
    project.pipeline_state = m.PipelineState.IN_PROGRESS
    pl.advance_if_ready(db, project)
    db.commit()


def test_all_nine_stages_run_and_produce_stage_runs(db):
    project = _make_project(db)
    pl.run_stage(db, project, m.Stage.AV)
    pl.advance_if_ready(db, project)
    pl.run_stage(db, project, m.Stage.RESEARCH)
    pl.advance_if_ready(db, project)
    pl.run_stage(db, project, m.Stage.FACT_CHECK)
    db.commit()
    _clear_fact_check_gate(db, project)

    assert project.current_stage == m.Stage.SCRIPT

    for stage in (m.Stage.SCRIPT, m.Stage.VOICE, m.Stage.VISUALS, m.Stage.COPYRIGHT,
                  m.Stage.THUMBNAIL, m.Stage.PUBLISH):
        pl.run_stage(db, project, stage)
        db.commit()
        pl.advance_if_ready(db, project)
        db.commit()

    runs = db.query(m.StageRun).filter(m.StageRun.project_id == project.id).all()
    ran_stages = {r.stage for r in runs}
    assert ran_stages == set(m.STAGE_ORDER)


def test_quality_gate_computed_after_publish_sets_pipeline_state(db):
    project = _make_project(db)
    for stage in (m.Stage.AV, m.Stage.RESEARCH, m.Stage.FACT_CHECK):
        pl.run_stage(db, project, stage)
        db.commit()
        if stage == m.Stage.FACT_CHECK:
            _clear_fact_check_gate(db, project)
        else:
            pl.advance_if_ready(db, project)
            db.commit()

    for stage in (m.Stage.SCRIPT, m.Stage.VOICE, m.Stage.VISUALS, m.Stage.COPYRIGHT,
                  m.Stage.THUMBNAIL, m.Stage.PUBLISH):
        pl.run_stage(db, project, stage)
        db.commit()
        pl.advance_if_ready(db, project)
        db.commit()

    gate = db.query(m.QualityGateResult).filter(m.QualityGateResult.project_id == project.id).first()
    assert gate is not None
    assert gate.overall_score > 0
    assert project.pipeline_state.value == gate.verdict
    assert project.overall_quality_score == gate.overall_score


def test_quality_gate_hard_fails_on_low_accuracy_regardless_of_verdict(db):
    """Spec section 30: never let a high overall score paper over an
    accuracy or copyright failure — enforced in code, not just prompted.
    Uses a fake LLM that (wrongly) says READY_TO_PUBLISH with a low accuracy
    score, and checks pipeline.compute_quality_gate overrides it anyway."""
    from app.agents.quality_gate import QualityGateAgent

    class _FakeHighScoreButLowAccuracyLLM:
        def complete_json(self, system, user, stage=None):
            return {
                "newsworthiness": 10, "accuracy": 1, "research": 10, "originality": 10,
                "script": 10, "voice": 10, "visuals": 10, "copyright": 10, "thumbnail": 10,
                "title": 10, "viewer_value": 10, "freshness": 10,
                "overall_score": 111, "verdict": "READY_TO_PUBLISH",
            }

    project = _make_project(db, current_stage=m.Stage.PUBLISH)
    fake_agent = QualityGateAgent(llm=_FakeHighScoreButLowAccuracyLLM())
    result = pl.compute_quality_gate(db, project, agent=fake_agent)
    db.commit()

    assert result.accuracy == 1.0
    assert result.overall_score == 111  # the raw LLM score is preserved for visibility...
    assert result.verdict == "DO_NOT_PUBLISH"  # ...but the verdict is overridden
    assert project.pipeline_state == m.PipelineState.DO_NOT_PUBLISH
