# AI YouTube Newsroom — scaffold

Implements, per the master build spec: the core data model, the 9-stage pipeline state
machine (all 9 agents wired with real prompts), the Final Quality Gate, scheduling/calendar,
Idea Vault, content repurposing, search, file uploads, and a dashboard UI. See
`ARCHITECTURE.md` for the design rationale and `SPEC_STATUS.md` for a section-by-section
breakdown of what's done vs. still partial against the original 40-section spec.

## What's real vs. stubbed

| Piece | Status |
|---|---|
| Data model (Project, StageRun, Source, Claim, Asset, QualityGateResult, ApprovalEvent, ScheduleConfig, IdeaVaultEntry) | **Fully implemented**, SQLAlchemy, tested |
| Pipeline state machine (gates, versioning, review-mode pausing, fact-check block/loop-back) | **Fully implemented**, tested |
| All 9 pipeline agents (AV, Research, Fact Check, Script, Voice, Visuals, Copyright, Thumbnail, Publish) | **Fully wired** — real prompts, real persistence (Sources/Claims/Assets). Run against `MockLLMClient` (deterministic offline stub) unless `ANTHROPIC_API_KEY` is set |
| Final Quality Gate (12-category scoring + code-enforced hard-fail rule) | **Implemented**, tested including the hard-fail override case |
| Scheduling engine + Content Calendar | **Implemented** — slot generation tested; see "Background scheduler" below for what actually executes it |
| Idea Vault (CRUD + promote-to-project) + Topic Discovery agent | **Implemented**, tested |
| Content Repurposing (Shorts from a full video's Publish output) | **Implemented**, tested end-to-end |
| Product Comparison Engine | **Implemented** — structured table from verified specs only |
| Automatic Research Refresh | **Implemented** — age-based re-check via `/refresh-research` |
| File uploads + Asset library persistence | **Implemented** — real files on disk, extension/size validation, Visuals/Copyright agents now write real Asset rows |
| Unboxing/Review first-party observation entry | **Implemented** — structured fields, feeds the Script agent's Observed-vs-Expected distinction |
| Background scheduler (executes the calendar automatically) | **Implemented but opt-in and single-replica-only** — in-process APScheduler, off by default (`ENABLE_SCHEDULER`) |
| Search / Filter | **Implemented** across projects/sources/claims |
| Dashboard / Queue / Calendar / Idea Vault / Create / Project Detail UI | **Fully implemented**, calls the live API, verified with a headless-browser test (zero JS runtime errors) |
| Production infra: env-driven config, Alembic migrations, Postgres support, Docker (backend+frontend+db), gunicorn, structured logging, API-key auth, automated tests | **Implemented** — see `DEPLOYMENT.md` |

Genuinely not yet built: an external topic-discovery data source (the Discovery agent reasons
from the LLM's own knowledge, not live search — see `agents/discovery.py`'s docstring), a
"Final Video Package" single-document export view, and deeper per-content-type behavior in
agents beyond Script. Full detail in `SPEC_STATUS.md`.

Github  action '`github.md`
# 🛠️ Core Commands (Quick Reference)

You can manage your virtual environment entirely from your terminal using Python's built-in `venv` module.

| Action | macOS / Linux | Windows (Command Prompt) | Windows (PowerShell) |
|---|---|---|---|
| **1. Create** | `python3 -m venv .venv` | `python -m venv .venv` | `python -m venv .venv` |
| **2. Activate** | `source .venv/bin/activate` | `.venv\Scripts\activate.bat` | `.venv\Scripts\Activate.ps1` |
| **3. Deactivate** | `deactivate` | `deactivate` | `deactivate` |

## Run it (local dev)

```bash
cd backend
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```
**For production deployment (Docker Compose, bare metal, migrations, secrets, gunicorn,
tests) see [`DEPLOYMENT.md`](./DEPLOYMENT.md).**

To see the pipeline run end-to-end without a browser:

```bash
cd backend
python seed_demo.py
```

This creates a demo project, runs AV → Research → Fact Check, and prints every StageRun,
Claim, and Source it created — including the fact-check gate correctly **blocking**
advancement (the mock fact-check score is deliberately below threshold, so you can see the
"STOP → RESEARCH" loop-back from spec section 11 actually fire).

For the full 9-stage run: `POST /api/projects/{id}/run-full-pipeline`.

## Go live with a real LLM

Set `ANTHROPIC_API_KEY` in the environment before starting the server — `agents/base.py`'s
`get_llm_client()` picks `AnthropicLLMClient` automatically when the key is present. No other
code changes needed; every agent already calls `self.llm.complete_json(system, user)`.

## Background scheduler

Off by default. Set `ENABLE_SCHEDULER=true` to run an in-process job (interval controlled by
`SCHEDULER_POLL_INTERVAL_SECONDS`) that fills open Calendar slots with the highest-opportunity
matching Idea Vault entry and runs it through the pipeline. **Only safe on a single backend
replica** — see `app/scheduler.py`'s docstring and `DEPLOYMENT.md`. You can also trigger a
single tick manually at any time via `POST /api/scheduler/tick`, which works regardless of
whether the background loop is enabled.

## Next slice to build

Roughly in priority order: (1) an external search/news-source integration for the Discovery
agent, so Topic Discovery reflects what's actually breaking today rather than the model's
training-time knowledge; (2) a "Final Video Package" export view assembling every stage's
output into one document per spec section 31; (3) deeper per-content-type branching in the
Research/Visuals/Publish agents, following the pattern already established in `script.py`.


