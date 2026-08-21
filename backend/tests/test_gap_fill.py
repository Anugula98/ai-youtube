"""Tests for the gap-filling features added after the first production
pass: file uploads, unboxing observations, asset library persistence from
Visuals/Copyright, the comparison engine, research refresh, and topic
discovery."""
import io
import datetime as dt
from pathlib import Path

from app import models as m
from app import pipeline as pl


# ---------------------------------------------------------------------------
# File uploads
# ---------------------------------------------------------------------------

def test_upload_rejects_disallowed_extension(client):
    resp = client.post("/api/projects", json={
        "title": "Upload test", "topic": "Upload test", "content_type": "UNBOXING",
    })
    project_id = resp.json()["id"]

    resp = client.post(
        f"/api/projects/{project_id}/upload",
        files={"file": ("malware.exe", io.BytesIO(b"fake binary"), "application/octet-stream")},
    )
    assert resp.status_code == 400


def test_upload_stores_file_and_creates_asset(client, tmp_path, monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()

    resp = client.post("/api/projects", json={
        "title": "Upload test 2", "topic": "Upload test 2", "content_type": "UNBOXING",
    })
    project_id = resp.json()["id"]

    resp = client.post(
        f"/api/projects/{project_id}/upload",
        files={"file": ("box_photo.jpg", io.BytesIO(b"\xff\xd8\xff\xfake jpeg bytes"), "image/jpeg")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["original_filename"] == "box_photo.jpg"
    assert body["size_bytes"] > 0

    resp = client.get(f"/api/projects/{project_id}/assets")
    assets = resp.json()
    assert len(assets) == 1
    assert assets[0]["is_user_upload"] is True
    assert assets[0]["original_filename"] == "box_photo.jpg"

    get_settings.cache_clear()


def test_upload_filename_traversal_payload_stays_inside_project_dir(client, tmp_path, monkeypatch):
    """The actually-exploitable case on this app's Linux deployment target:
    a filename containing '../' components must not escape upload_dir. Does
    NOT test backslash-style traversal -- see _safe_upload_path's docstring
    for why that's a non-issue on POSIX specifically."""
    from app.config import get_settings
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()

    resp = client.post("/api/projects", json={
        "title": "Traversal test", "topic": "Traversal test", "content_type": "UNBOXING",
    })
    project_id = resp.json()["id"]

    resp = client.post(
        f"/api/projects/{project_id}/upload",
        files={"file": ("../../../../etc/passwd.jpg", io.BytesIO(b"payload"), "image/jpeg")},
    )
    assert resp.status_code == 200

    # Nothing was written anywhere outside tmp_path (the configured upload_dir).
    escaped_files = list(tmp_path.parent.glob("passwd.jpg")) + list(Path("/etc").glob("passwd.jpg"))
    assert escaped_files == []
    # The file genuinely was written, just safely inside the project's own dir.
    written_files = list((tmp_path / str(project_id)).glob("*passwd.jpg"))
    assert len(written_files) == 1

    get_settings.cache_clear()


def test_upload_rejects_oversized_file(client, tmp_path, monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "0")  # effectively 0 bytes allowed
    get_settings.cache_clear()

    resp = client.post("/api/projects", json={
        "title": "Oversize test", "topic": "Oversize test", "content_type": "UNBOXING",
    })
    project_id = resp.json()["id"]

    resp = client.post(
        f"/api/projects/{project_id}/upload",
        files={"file": ("big.jpg", io.BytesIO(b"x" * 2000), "image/jpeg")},
    )
    assert resp.status_code == 413

    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Unboxing observations
# ---------------------------------------------------------------------------

def test_set_and_get_observations(client):
    resp = client.post("/api/projects", json={
        "title": "Observations test", "topic": "Observations test", "content_type": "UNBOXING",
    })
    project_id = resp.json()["id"]

    resp = client.put(f"/api/projects/{project_id}/observations", json={
        "box_condition": "Sealed, minor corner dent",
        "included_accessories": "USB-C cable only, no charger",
        "weight": "212g, feels heavier than spec sheet suggests",
    })
    assert resp.status_code == 200

    resp = client.get(f"/api/projects/{project_id}/observations")
    obs = resp.json()
    assert obs["box_condition"] == "Sealed, minor corner dent"


def test_set_observations_rejects_unknown_field(client):
    resp = client.post("/api/projects", json={
        "title": "Bad observations", "topic": "Bad observations", "content_type": "UNBOXING",
    })
    project_id = resp.json()["id"]
    resp = client.put(f"/api/projects/{project_id}/observations", json={"made_up_field": "x"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Asset library persistence (Visuals -> Copyright)
# ---------------------------------------------------------------------------

def test_visuals_and_copyright_persist_and_update_assets(db):
    project = m.Project(
        title="Asset test", topic="Asset test", content_type=m.ContentType.NEWS,
        pipeline_mode=m.PipelineMode.AUTO, current_stage=m.Stage.AV,
        pipeline_state=m.PipelineState.NOT_STARTED,
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    for stage in (m.Stage.AV, m.Stage.RESEARCH):
        pl.run_stage(db, project, stage)
        db.commit()
        pl.advance_if_ready(db, project)
        db.commit()

    # Force past the fact-check gate the same way test_full_pipeline.py does.
    pl.run_stage(db, project, m.Stage.FACT_CHECK)
    db.commit()
    run = pl._get_current_stage_run(db, project.id, m.Stage.FACT_CHECK)
    run.score = 90.0
    project.current_stage = m.Stage.FACT_CHECK
    project.pipeline_state = m.PipelineState.IN_PROGRESS
    pl.advance_if_ready(db, project)
    db.commit()

    for stage in (m.Stage.SCRIPT, m.Stage.VOICE, m.Stage.VISUALS):
        pl.run_stage(db, project, stage)
        db.commit()
        pl.advance_if_ready(db, project)
        db.commit()

    assets_after_visuals = db.query(m.Asset).filter(m.Asset.project_id == project.id).all()
    assert len(assets_after_visuals) >= 1
    assert assets_after_visuals[0].copyright_notes == "First-pass estimate from the Visuals agent — not yet independently reviewed."

    pl.run_stage(db, project, m.Stage.COPYRIGHT)
    db.commit()

    assets_after_copyright = db.query(m.Asset).filter(m.Asset.project_id == project.id).all()
    assert assets_after_copyright[0].copyright_notes != "First-pass estimate from the Visuals agent — not yet independently reviewed."


# ---------------------------------------------------------------------------
# Comparison engine
# ---------------------------------------------------------------------------

def test_compare_requires_at_least_two_projects(client):
    resp = client.post("/api/projects", json={"title": "A", "topic": "A", "content_type": "PRODUCT_REVIEW"})
    pid = resp.json()["id"]
    resp = client.post("/api/compare", json=[pid])
    assert resp.status_code == 400


def test_compare_returns_structured_table(client):
    ids = []
    for name in ("Phone A", "Phone B"):
        resp = client.post("/api/projects", json={"title": name, "topic": name, "content_type": "PRODUCT_COMPARISON"})
        ids.append(resp.json()["id"])
    resp = client.post("/api/compare", json=ids)
    assert resp.status_code == 200
    body = resp.json()
    assert "table" in body


def test_compare_on_projects_with_no_research_does_not_crash(client):
    """Edge case: comparing projects that never had Research run -- each
    project's specifications should degrade to {} rather than crashing."""
    ids = []
    for name in ("No research A", "No research B"):
        resp = client.post("/api/projects", json={"title": name, "topic": name, "content_type": "PRODUCT_COMPARISON"})
        ids.append(resp.json()["id"])
    resp = client.post("/api/compare", json=ids)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Research refresh
# ---------------------------------------------------------------------------

def test_refresh_research_is_noop_when_fresh(client):
    resp = client.post("/api/projects", json={"title": "Fresh", "topic": "Fresh", "content_type": "NEWS"})
    pid = resp.json()["id"]
    client.post(f"/api/projects/{pid}/run-stage")  # AV
    client.post(f"/api/projects/{pid}/approve", json={"action": "APPROVE"})
    client.post(f"/api/projects/{pid}/run-stage")  # RESEARCH

    resp = client.post(f"/api/projects/{pid}/refresh-research", params={"max_age_hours": 24})
    assert resp.status_code == 200
    # Should still be exactly 1 RESEARCH stage run (no refresh triggered)
    runs = [r for r in resp.json()["stage_runs"] if r["stage"] == "RESEARCH"]
    assert len(runs) == 1


def test_refresh_research_reruns_when_stale(client, db):
    resp = client.post("/api/projects", json={"title": "Stale", "topic": "Stale", "content_type": "NEWS"})
    pid = resp.json()["id"]
    client.post(f"/api/projects/{pid}/run-stage")  # AV
    client.post(f"/api/projects/{pid}/approve", json={"action": "APPROVE"})
    client.post(f"/api/projects/{pid}/run-stage")  # RESEARCH

    # Backdate the Research StageRun's created_at to simulate staleness.
    run = db.query(m.StageRun).filter(
        m.StageRun.project_id == pid, m.StageRun.stage == m.Stage.RESEARCH
    ).first()
    run.created_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=48)
    db.commit()

    resp = client.post(f"/api/projects/{pid}/refresh-research", params={"max_age_hours": 24})
    assert resp.status_code == 200
    runs = [r for r in resp.json()["stage_runs"] if r["stage"] == "RESEARCH"]
    assert len(runs) == 2  # a new version was created


# ---------------------------------------------------------------------------
# Topic discovery
# ---------------------------------------------------------------------------

def test_discover_topics_populates_idea_vault(client):
    resp = client.post("/api/idea-vault/discover", params={"category": "smartphones", "count": 2})
    assert resp.status_code == 200
    ideas = resp.json()
    assert len(ideas) == 2

    resp = client.get("/api/idea-vault")
    vault = resp.json()
    assert len(vault) >= 2


# ---------------------------------------------------------------------------
# Background scheduler tick (called directly rather than waiting on real
# background timing -- see app/scheduler.py)
# ---------------------------------------------------------------------------

def test_scheduler_tick_fills_open_slot_with_matching_idea(client, db):
    # Configure a schedule with one Short slot exactly "now" (within the
    # tick's lookahead window) so the tick has something to fill.
    now = dt.datetime.now().replace(second=0, microsecond=0)
    config = m.ScheduleConfig(
        full_videos_per_day=0, shorts_per_day=1, shorts_interval_minutes=1440,
        publishing_window_start=now.strftime("%H:%M"),
        publishing_window_end=(now + dt.timedelta(minutes=1)).strftime("%H:%M"),
    )
    db.add(config)
    db.commit()

    # A NEW Short idea should get picked up by the tick.
    idea = m.IdeaVaultEntry(
        topic="Scheduler-filled Short topic", content_type=m.ContentType.SHORT,
        opportunity_score=80, status="NEW",
    )
    db.add(idea)
    db.commit()
    db.refresh(idea)

    from app import scheduler as bg_scheduler
    actions = bg_scheduler.run_scheduler_tick(db, now=now, lookahead_minutes=15)

    assert len(actions) == 1
    assert actions[0]["idea_id"] == idea.id
    db.refresh(idea)
    assert idea.status == "QUEUED"

    project = db.get(m.Project, actions[0]["project_id"])
    assert project.content_type == m.ContentType.SHORT
    assert project.scheduled_publish_at is not None
    # AUTO mode means the tick should have kicked off the pipeline immediately.
    assert project.pipeline_state != m.PipelineState.NOT_STARTED


def test_scheduler_tick_is_noop_with_no_matching_idea(client, db):
    now = dt.datetime.now().replace(second=0, microsecond=0)
    config = m.ScheduleConfig(
        full_videos_per_day=0, shorts_per_day=1, shorts_interval_minutes=1440,
        publishing_window_start=now.strftime("%H:%M"),
        publishing_window_end=(now + dt.timedelta(minutes=1)).strftime("%H:%M"),
    )
    db.add(config)
    db.commit()

    from app import scheduler as bg_scheduler
    actions = bg_scheduler.run_scheduler_tick(db, now=now, lookahead_minutes=15)
    assert actions == []


def test_scheduler_tick_does_not_double_book_an_already_scheduled_slot(client, db):
    now = dt.datetime.now().replace(second=0, microsecond=0)
    config = m.ScheduleConfig(
        full_videos_per_day=0, shorts_per_day=1, shorts_interval_minutes=1440,
        publishing_window_start=now.strftime("%H:%M"),
        publishing_window_end=(now + dt.timedelta(minutes=1)).strftime("%H:%M"),
    )
    db.add(config)

    existing = m.Project(
        title="Already scheduled", topic="Already scheduled", content_type=m.ContentType.SHORT,
        pipeline_mode=m.PipelineMode.AUTO, current_stage=m.Stage.AV,
        pipeline_state=m.PipelineState.NOT_STARTED, scheduled_publish_at=now,
    )
    db.add(existing)

    idea = m.IdeaVaultEntry(topic="Should stay NEW", content_type=m.ContentType.SHORT,
                             opportunity_score=90, status="NEW")
    db.add(idea)
    db.commit()

    from app import scheduler as bg_scheduler
    actions = bg_scheduler.run_scheduler_tick(db, now=now, lookahead_minutes=15)
    assert actions == []  # slot already has a project — nothing to fill
    db.refresh(idea)
    assert idea.status == "NEW"  # untouched


# ---------------------------------------------------------------------------
# Scheduler lock (multi-replica safety — spec section 32's documented gap)
# ---------------------------------------------------------------------------

def test_scheduler_lock_prevents_concurrent_tick_execution(db):
    """Simulates two backend replicas racing on the same tick: the second
    acquire attempt (while the first holder's lease is still active) must
    fail, and the tick must return no actions when it can't get the lock --
    this is what actually makes ENABLE_SCHEDULER=true safe on >1 replica."""
    from app import scheduler_lock

    holder_a = scheduler_lock.try_acquire(db, holder_id="replica-a", lease_seconds=60)
    assert holder_a == "replica-a"

    holder_b = scheduler_lock.try_acquire(db, holder_id="replica-b", lease_seconds=60)
    assert holder_b is None  # replica-a's lease hasn't expired — replica-b must not get the lock

    scheduler_lock.release(db, "replica-a")
    holder_b_retry = scheduler_lock.try_acquire(db, holder_id="replica-b", lease_seconds=60)
    assert holder_b_retry == "replica-b"  # now free, replica-b gets it


def test_scheduler_lock_expires_and_can_be_reacquired(db):
    from app import scheduler_lock
    import time as _time

    holder_a = scheduler_lock.try_acquire(db, holder_id="replica-a", lease_seconds=0)
    assert holder_a == "replica-a"
    _time.sleep(0.05)  # ensure "now" on the next call is past the (already-expired) lease

    holder_b = scheduler_lock.try_acquire(db, holder_id="replica-b", lease_seconds=60)
    assert holder_b == "replica-b"  # replica-a's lease already expired — replica-b can take over


def test_run_scheduler_tick_returns_empty_when_lock_held(db):
    """End-to-end: even with a real open slot and a real matching idea, a
    tick that can't acquire the lock must do nothing at all."""
    from app import scheduler as bg_scheduler
    from app import scheduler_lock

    now = dt.datetime.now().replace(second=0, microsecond=0)
    config = m.ScheduleConfig(
        full_videos_per_day=0, shorts_per_day=1, shorts_interval_minutes=1440,
        publishing_window_start=now.strftime("%H:%M"),
        publishing_window_end=(now + dt.timedelta(minutes=1)).strftime("%H:%M"),
    )
    db.add(config)
    idea = m.IdeaVaultEntry(topic="Locked out topic", content_type=m.ContentType.SHORT,
                             opportunity_score=99, status="NEW")
    db.add(idea)
    db.commit()

    # Simulate another replica already holding the lock.
    other_holder = scheduler_lock.try_acquire(db, holder_id="other-replica", lease_seconds=60)
    assert other_holder == "other-replica"

    actions = bg_scheduler.run_scheduler_tick(db, now=now, lookahead_minutes=15, use_lock=True)
    assert actions == []
    db.refresh(idea)
    assert idea.status == "NEW"  # nothing was promoted — the tick never ran


# ---------------------------------------------------------------------------
# Final Video Package (spec section 31)
# ---------------------------------------------------------------------------

def test_final_package_assembles_all_stage_outputs(client, monkeypatch, request):
    from app.config import get_settings
    monkeypatch.setenv("FACT_CHECK_THRESHOLD", "10")
    get_settings.cache_clear()
    request.addfinalizer(get_settings.cache_clear)

    resp = client.post("/api/projects", json={
        "title": "Package test", "topic": "Package test topic",
        "content_type": "AI_NEWS", "pipeline_mode": "AUTO",
    })
    project_id = resp.json()["id"]
    client.post(f"/api/projects/{project_id}/run-full-pipeline")

    resp = client.get(f"/api/projects/{project_id}/final-package")
    assert resp.status_code == 200
    pkg = resp.json()

    assert pkg["story"]["topic"] == "Package test topic"
    assert "facts" in pkg["research"]
    assert "sources" in pkg["research"]
    assert "claims" in pkg["fact_check"]
    assert pkg["script"].get("full_script") is not None
    assert pkg["publish"].get("title") is not None
    assert pkg["quality_gate"] is not None
    assert pkg["quality_gate"]["overall_score"] > 0
    assert pkg["pipeline_state"] in ("READY_TO_PUBLISH", "NEEDS_REVIEW", "DO_NOT_PUBLISH", "BLOCKED")


def test_final_package_includes_product_details_only_for_product_types(client):
    resp = client.post("/api/projects", json={
        "title": "News package", "topic": "News package", "content_type": "NEWS",
    })
    pid = resp.json()["id"]
    resp = client.get(f"/api/projects/{pid}/final-package")
    assert resp.json()["product_details"] is None

    resp = client.post("/api/projects", json={
        "title": "Unboxing package", "topic": "Unboxing package", "content_type": "UNBOXING",
    })
    pid2 = resp.json()["id"]
    resp = client.get(f"/api/projects/{pid2}/final-package")
    assert resp.json()["product_details"] is not None


def test_final_package_404_for_missing_project(client):
    resp = client.get("/api/projects/999999/final-package")
    assert resp.status_code == 404


def test_final_package_on_brand_new_project_has_no_data_but_does_not_crash(client):
    """Edge case: a project with zero StageRuns yet (just created, nothing
    run). Every section should degrade to an empty/null default rather than
    KeyError or 500."""
    resp = client.post("/api/projects", json={"title": "Blank", "topic": "Blank", "content_type": "NEWS"})
    pid = resp.json()["id"]
    resp = client.get(f"/api/projects/{pid}/final-package")
    assert resp.status_code == 200
    pkg = resp.json()
    assert pkg["quality_gate"] is None
    assert pkg["script"] == {}
    assert pkg["research"]["facts"] == []


# ---------------------------------------------------------------------------
# Research agent product/unboxing mode branching (spec section 10)
# ---------------------------------------------------------------------------

def test_research_agent_uses_product_prompt_for_unboxing():
    from app.agents.research import ResearchAgent, PRODUCT_SYSTEM_PROMPT, STANDARD_SYSTEM_PROMPT

    captured = {}

    class _CapturingLLM:
        def complete_json(self, system, user, stage=None):
            captured["system"] = system
            captured["user"] = user
            return {"facts": [], "sources": []}

    project = m.Project(
        title="Unboxing", topic="Unboxing topic", content_type=m.ContentType.UNBOXING,
        product_info={"user_observations": {"box_condition": "Sealed"}},
    )
    agent = ResearchAgent(llm=_CapturingLLM())
    agent.run(project, context={})

    assert captured["system"] == PRODUCT_SYSTEM_PROMPT
    assert "user_observations" in captured["user"] or "Sealed" in captured["user"]


def test_research_agent_uses_standard_prompt_for_news():
    from app.agents.research import ResearchAgent, STANDARD_SYSTEM_PROMPT

    captured = {}

    class _CapturingLLM:
        def complete_json(self, system, user, stage=None):
            captured["system"] = system
            return {"facts": [], "sources": []}

    project = m.Project(title="News", topic="News topic", content_type=m.ContentType.NEWS)
    agent = ResearchAgent(llm=_CapturingLLM())
    agent.run(project, context={})

    assert captured["system"] == STANDARD_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Discovery agent search-provider grounding
# ---------------------------------------------------------------------------

def test_discovery_agent_ungrounded_by_default(client):
    """No provider configured (the standard test environment) -- confirms
    the existing discover endpoint still works exactly as before this change."""
    resp = client.post("/api/idea-vault/discover", params={"category": "gpus", "count": 2})
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_discovery_agent_grounds_prompt_when_provider_has_results():
    from app.agents.discovery import DiscoveryAgent

    captured = {}

    class _CapturingLLM:
        def complete_json(self, system, user, stage=None):
            captured["system"] = system
            captured["user"] = user
            return {"ideas": []}

    class _FakeGroundedProvider:
        def search(self, query, max_results=10):
            return [{"title": "Real breaking headline", "snippet": "Something happened",
                      "url": "https://example.com/a", "published_at": "2026-08-13"}]

    agent = DiscoveryAgent(llm=_CapturingLLM(), search_provider=_FakeGroundedProvider())
    agent.run(category="smartphones", count=3)

    assert "ground your proposals" in captured["system"]
    assert "Real breaking headline" in captured["user"]


def test_discovery_agent_ungrounded_when_provider_returns_nothing():
    from app.agents.discovery import DiscoveryAgent
    from app.agents.search_provider import NullSearchProvider

    captured = {}

    class _CapturingLLM:
        def complete_json(self, system, user, stage=None):
            captured["system"] = system
            captured["user"] = user
            return {"ideas": []}

    agent = DiscoveryAgent(llm=_CapturingLLM(), search_provider=NullSearchProvider())
    agent.run(category="smartphones", count=3)

    assert "ground your proposals" not in captured["system"]
    assert "Recent headlines" not in captured["user"]


def test_discovery_agent_degrades_gracefully_when_search_provider_raises():
    """A configured search provider is optional grounding, not a hard
    dependency -- if it throws (the realistic failure mode for any real
    external API: network error, bad key, rate limit), the agent must fall
    back to ungrounded discovery rather than propagating the exception and
    502/500-ing the whole /idea-vault/discover endpoint."""
    from app.agents.discovery import DiscoveryAgent

    class _CapturingLLM:
        def complete_json(self, system, user, stage=None):
            return {"ideas": [{"topic": "fallback worked", "opportunity_score": 50}]}

    class _FailingProvider:
        def search(self, query, max_results=10):
            raise ConnectionError("simulated network failure")

    agent = DiscoveryAgent(llm=_CapturingLLM(), search_provider=_FailingProvider())
    ideas = agent.run(category="smartphones", count=3)  # must not raise

    assert ideas == [{"topic": "fallback worked", "opportunity_score": 50}]


def test_discover_endpoint_does_not_500_when_search_provider_raises(client, monkeypatch):
    """Same failure mode as above, exercised through the real HTTP endpoint
    end-to-end -- confirms the degradation path holds all the way up, not
    just at the agent's own unit-test level."""
    import app.main as main_module

    class _FailingProvider:
        def search(self, query, max_results=10):
            raise ConnectionError("simulated network failure")

    from app.agents.discovery import DiscoveryAgent
    original_init = DiscoveryAgent.__init__

    def _patched_init(self, llm=None, search_provider=None):
        original_init(self, llm=llm, search_provider=_FailingProvider())

    monkeypatch.setattr(DiscoveryAgent, "__init__", _patched_init)

    resp = client.post("/api/idea-vault/discover", params={"category": "gpus", "count": 2})
    assert resp.status_code == 200  # not 500 -- degraded gracefully
    assert len(resp.json()) == 2
