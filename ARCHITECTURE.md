# AI YouTube Newsroom — Architecture

## 1. Design principles

- **Everything is a Project.** A Project is the single unit of work. It moves through a fixed
  pipeline of Stages. Nothing about a Project's history is ever deleted — every agent run
  creates a new immutable `StageRun` (a version), and the Project just points at which run is
  "current" for each stage.
- **The pipeline is a state machine, not a script.** A Project has a `pipeline_state` and a
  `current_stage`. Stages can only advance forward when the prior stage's gate passes
  (fact-check score, copyright score, quality threshold). A stage can be re-run at any time,
  which produces a new `StageRun` version without destroying old ones.
- **Agents are pure functions over context.** Every agent takes `(project, upstream_stage_outputs,
  user_overrides) -> StageOutput`. This is what makes them swappable between a stub/mock
  implementation (for local dev, demoed below) and a real LLM-backed implementation
  (`LLMClient` in `agents/llm_client.py`) without touching the orchestrator.
- **Claims and Sources are first-class rows, not blobs inside a script.** Fact-check scoring,
  the "Sources" tab, and the copyright/legal review all need to query claims independently of
  which script version currently references them.
- **Quality gates override quantity targets.** The scheduler only ever *proposes* what to
  produce next; the pipeline's own gate logic is what allows a project to reach `READY_TO_PUBLISH`.

## 2. Core entities (SQLAlchemy models — see `backend/app/models.py`)

```
Project
  id, title, topic, content_type (enum: NEWS/BREAKING/LONG_FORM/SHORT/REVIEW/UNBOXING/
      FEATURES/COMPARISON/BUYING_GUIDE/EXPLAINER/RUMOR/SOFTWARE_OS/AI_NEWS)
  duration_target_seconds, target_audience, tone, language
  user_instructions, source_urls (JSON list), product_info (JSON)
  priority (enum: BREAKING/HIGH_DEMAND/LAUNCH/TRENDING/EVERGREEN/LOW)
  pipeline_mode (enum: AUTO / REVIEW)
  current_stage (enum: AV/RESEARCH/FACT_CHECK/SCRIPT/VOICE/VISUALS/COPYRIGHT/THUMBNAIL/PUBLISH)
  pipeline_state (enum: NOT_STARTED/IN_PROGRESS/NEEDS_REVIEW/BLOCKED/READY_TO_PUBLISH/
      PUBLISHED/DO_NOT_PUBLISH/REJECTED)
  freshness_score, overall_quality_score
  scheduled_publish_at, published_at
  created_at, updated_at

StageRun                       # one immutable version of one stage's output
  id, project_id, stage (enum, same as current_stage), version_number
  status (enum: PENDING/RUNNING/SUCCEEDED/FAILED/NEEDS_REVIEW)
  input_snapshot (JSON)        # exactly what the agent saw
  output (JSON)                # the agent's structured output
  score (nullable float)       # e.g. fact-check score, copyright score
  is_current (bool)            # only one StageRun per (project, stage) has is_current=True
  created_at

Source
  id, project_id, url, title, publisher, source_tier (enum: OFFICIAL/COMPANY/PRODUCT_PAGE/
      REGULATORY/DEV_DOCS/REPUTABLE_PRESS/SPECIALIST_PRESS/COMMUNITY)
  retrieved_at, notes

Claim
  id, project_id, stage_run_id (which Research StageRun produced it), text
  claim_type (spec/price/date/availability/quote/feature/performance/benchmark/other)
  verification_status (enum: VERIFIED/PARTIALLY_VERIFIED/REPORTED/RUMOR/CONTRADICTED/
      UNSUPPORTED/FALSE)
  confidence (0-100), source_ids (JSON list of Source.id)
  fact_checked_at

Asset
  id, project_id, stage_run_id, asset_type (image/video/audio/graphic/thumbnail),
  url_or_path, copyright_status (enum: LOW/MEDIUM/HIGH), copyright_notes
  timestamp_in_video (nullable — for storyboard assets)

QualityGateResult
  id, project_id, stage_run_id
  newsworthiness, accuracy, research, originality, script, voice, visuals,
  copyright, thumbnail, title, viewer_value, freshness   # each /10
  overall_score  # /120
  verdict (enum: READY_TO_PUBLISH/NEEDS_REVIEW/RESEARCH_REQUIRED/DO_NOT_PUBLISH)

ApprovalEvent
  id, project_id, stage, action (enum: APPROVE/EDIT/REGENERATE/SEND_BACK/REJECT)
  actor, note, created_at

ScheduleConfig
  id, full_videos_per_day, shorts_per_day, shorts_interval_minutes
  publishing_window_start, publishing_window_end
  weekday_overrides (JSON), weekend_overrides (JSON)
  quality_threshold, max_daily_output

IdeaVaultEntry
  id, topic, source, category, content_type, priority, opportunity_score
  suggested_title, status (NEW/RESEARCHING/QUEUED/IN_PRODUCTION/PUBLISHED/REJECTED/EXPIRED)
  created_at, expires_at
```

## 3. Pipeline state machine

```
AV -> RESEARCH -> FACT_CHECK -> SCRIPT -> VOICE -> VISUALS -> COPYRIGHT -> THUMBNAIL -> PUBLISH
```

Transition rule (`backend/app/pipeline.py::advance`):

1. Run the agent for `current_stage`, producing a new `StageRun` (version = max+1 for that
   project+stage), marked `is_current=True` (previous current for that stage flips to False —
   but is NOT deleted).
2. Evaluate the stage's gate:
   - `FACT_CHECK`: block advance if `fact_check_score < threshold` → `pipeline_state = BLOCKED`,
     `current_stage` stays at `FACT_CHECK` (loops back to `RESEARCH` for re-research).
   - `COPYRIGHT`: risk `HIGH` on any asset → `NEEDS_REVIEW`, halts auto-advance.
   - Every stage: if `pipeline_mode == REVIEW` and stage in
     `{RESEARCH, FACT_CHECK, SCRIPT, COPYRIGHT}` → set `NEEDS_REVIEW` and stop, regardless of
     score, until an `ApprovalEvent(action=APPROVE)` is recorded.
3. If gate passes and mode is `AUTO` (or stage was just approved in `REVIEW`), move
   `current_stage` to the next stage in the sequence.
4. At `PUBLISH`, run `QualityGateResult` computation; if `overall_score` < threshold *or* any
   individual category is 0/10 on a hard-fail dimension (accuracy, copyright) →
   `DO_NOT_PUBLISH` regardless of the daily quantity target.

This is intentionally implemented as an explicit `Stage` enum + adjacency, not a generic
graph — the pipeline is fixed by spec (section 1 of the master prompt), so a state machine is
simpler to reason about and test than a DAG engine.

## 4. Agent interface

```python
class Agent(Protocol):
    stage: Stage
    def run(self, project: Project, context: PipelineContext) -> StageOutput: ...
```

`PipelineContext` bundles every upstream `StageRun.output` the agent is allowed to see (e.g.
Script agent sees AV + Research + FactCheck outputs, never raw Research before FactCheck has
run). Each agent module returns a **typed** `StageOutput` Pydantic model, validated before it's
persisted — this is what lets the Fact Check agent, for instance, refuse to write a StageRun
whose claims don't have `source_ids` pointing at real `Source` rows.

`llm_client.py` defines `LLMClient.complete(system, messages) -> str`, backed by the Anthropic
API (`ANTHROPIC_API_KEY` env var). Agents in this scaffold default to `MockLLMClient`, which
returns deterministic structured stubs so the whole pipeline is runnable offline without a key.
Swap `MockLLMClient()` for `AnthropicLLMClient()` in `agents/base.py` to go live — no other code
changes.

## 5. Vertical slice implemented in this scaffold

`Create Project → AV → Research → Fact Check`, fully wired: FastAPI endpoints, SQLite
persistence, real gate logic (fact-check score threshold blocks advancement), and a seed script
that runs it end-to-end and prints the resulting state. Voice/Visuals/Copyright/Thumbnail/Publish
agents are stubbed with the same interface so extending the slice is additive, not a rewrite.

## 6. Frontend shell

Single-page dashboard (`frontend/index.html`, vanilla JS + Tailwind CDN — no build step needed)
implementing: Today metrics strip, Active Pipeline view (per-stage progress dots, matching
section 25 of the spec), Content Queue table, and a Project Detail view with tabs
(Overview/AV/Research/Fact Check/Sources/Claims). It calls the FastAPI backend directly
(`fetch('/api/...')`), so running `uvicorn` and opening the HTML file gives you a working,
if visually minimal, newsroom UI.

## 7. What was added after this document was first written

This document describes the original design. Everything below was added across three later
build passes — kept here as an addendum rather than folded into the sections above, so this
stays an accurate record of the initial design intent as well as what actually got built.

**All 9 pipeline agents** (Script, Voice, Visuals, Copyright, Thumbnail, Publish were
originally stubs) now have real prompts — see `agents/*.py`, each with a docstring explaining
its exact spec-section mapping. The mock-LLM dispatch (`agents/base.py`) is keyed off the
`Stage` enum, not prompt-text substring matching, after an early version of substring matching
caused a real bug (one agent's prompt happened to mention another agent's name).

**Final Quality Gate** (`agents/quality_gate.py`, `pipeline.compute_quality_gate`) — a
cross-cutting evaluation, not one of the 9 `STAGE_ORDER` stages, run once a project reaches
the end of the pipeline. 12-category scoring with a hard-fail rule enforced in code (not just
prompted): a low accuracy or copyright score forces `DO_NOT_PUBLISH` regardless of what the
LLM's verdict said.

**Scheduling** (`scheduling.py` for slot computation, `scheduler.py` for actually executing
it, `scheduler_lock.py` for multi-replica safety) — `ScheduleConfig` drives a proposed daily
slot layout (Shorts + full videos); `run_scheduler_tick` fills open slots from the Idea Vault
and runs the pipeline, protected by a DB-backed mutex so it's safe to enable
(`ENABLE_SCHEDULER=true`) on more than one backend replica.

**Idea Vault, Content Repurposing, Search, File uploads, first-party Observations, the
Comparison engine, Research Refresh, and the Final Video Package export** are each a
self-contained addition — see `main.py`'s route groupings (each has a section comment) and
`SPEC_STATUS.md` for the section-by-section mapping back to the original master spec.

**Production infrastructure** — `config.py` (env-driven settings), Alembic migrations
(replacing the original `create_all`), Docker (backend + frontend + Postgres), gunicorn,
structured logging, API-key auth — is covered in `DEPLOYMENT.md`, not here, since it's
deployment concern rather than application architecture.

For the fullest, most current picture of what's implemented vs. still partial, read
`SPEC_STATUS.md` — it's updated every pass and is the source of truth on completeness; this
document is the source of truth on *why* things are shaped the way they are.
