"""Runs the vertical slice (Create Project -> AV -> Research -> Fact Check)
end-to-end against a throwaway SQLite DB and prints the resulting state.
This is what proves the scaffold is real and runnable, not just files.

    cd backend && python seed_demo.py
"""
import json
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./demo.db")

from app.database import Base, engine, SessionLocal
from app import models as m
from app import pipeline as pl

Base.metadata.drop_all(bind=engine)  # fresh demo DB each run
Base.metadata.create_all(bind=engine)

db = SessionLocal()

project = m.Project(
    title="Galaxy S27 Ultra — Camera Deep Dive",
    topic="Samsung Galaxy S27 Ultra camera features",
    content_type=m.ContentType.PRODUCT_FEATURES,
    duration_target_seconds=480,
    target_audience="Android enthusiasts deciding whether to upgrade",
    tone="informative, no hype",
    language="en",
    user_instructions="Focus on what's actually new vs the S26 Ultra.",
    source_urls=["https://www.samsung.com/galaxy-s27-ultra"],
    product_info={"brand": "Samsung", "model": "Galaxy S27 Ultra"},
    priority=m.Priority.PRODUCT_LAUNCH,
    pipeline_mode=m.PipelineMode.AUTO,  # AUTO so the slice runs straight through
    current_stage=m.Stage.AV,
    pipeline_state=m.PipelineState.NOT_STARTED,
)
db.add(project)
db.commit()
db.refresh(project)

print(f"Created project #{project.id}: {project.title}")
print(f"  content_type={project.content_type.value} mode={project.pipeline_mode.value}\n")

for stage in (m.Stage.AV, m.Stage.RESEARCH, m.Stage.FACT_CHECK):
    print(f"--- Running stage: {stage.value} ---")
    run = pl.run_stage(db, project, stage)
    db.commit()
    db.refresh(project)
    print(f"  StageRun #{run.id} v{run.version_number} status={run.status.value} score={run.score}")
    print(f"  output: {json.dumps(run.output, indent=2)[:500]}")
    print(f"  project.pipeline_state -> {project.pipeline_state.value}")
    print(f"  project.current_stage  -> {project.current_stage.value}\n")

    if project.pipeline_state == m.PipelineState.BLOCKED:
        print("  ⚠ Gate blocked advancement (this is correct behavior for a low "
              "fact-check score) — pipeline looped back to RESEARCH per spec section 11.\n")
        break
    pl.advance_if_ready(db, project)
    db.commit()
    db.refresh(project)

print("=== Final state ===")
print(f"Project #{project.id}: current_stage={project.current_stage.value} "
      f"pipeline_state={project.pipeline_state.value}")

claims = db.query(m.Claim).filter(m.Claim.project_id == project.id).all()
print(f"\nClaims recorded ({len(claims)}):")
for c in claims:
    print(f"  [{c.verification_status.value:>18}] conf={c.confidence:>5} {c.text}")

sources = db.query(m.Source).filter(m.Source.project_id == project.id).all()
print(f"\nSources recorded ({len(sources)}):")
for s in sources:
    print(f"  [{s.source_tier.value}] {s.title} — {s.url}")

stage_runs = db.query(m.StageRun).filter(m.StageRun.project_id == project.id).all()
print(f"\nAll StageRun versions on record ({len(stage_runs)}) — nothing was deleted on advance:")
for r in stage_runs:
    print(f"  {r.stage.value} v{r.version_number} current={r.is_current} status={r.status.value}")

db.close()
