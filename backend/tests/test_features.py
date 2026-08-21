"""Tests for the newer feature layer: scheduling/calendar, idea vault,
content repurposing, and search."""
import datetime as dt

from app import models as m
from app import scheduling as sched


# ---------------------------------------------------------------------------
# Scheduling engine (pure functions, no DB needed for slot generation)
# ---------------------------------------------------------------------------

def test_generate_daily_slots_respects_interval_and_window():
    config = m.ScheduleConfig(
        full_videos_per_day=1, shorts_per_day=99, shorts_interval_minutes=30,
        publishing_window_start="09:00", publishing_window_end="10:30",
    )
    slots = sched.generate_daily_slots(config, dt.date(2026, 8, 13))
    short_slots = [s for s in slots if s["content_kind"] == "SHORT"]
    full_slots = [s for s in slots if s["content_kind"] == "FULL_VIDEO"]

    # 09:00, 09:30, 10:00 within a 90-minute window at 30-minute intervals
    assert len(short_slots) == 3
    assert len(full_slots) == 1
    assert slots == sorted(slots, key=lambda s: s["time"])


def test_generate_daily_slots_applies_weekend_override():
    config = m.ScheduleConfig(
        full_videos_per_day=1, shorts_interval_minutes=60,
        publishing_window_start="09:00", publishing_window_end="17:00",
        weekend_overrides={"shorts_interval_minutes": 120},
    )
    saturday = dt.date(2026, 8, 15)  # confirmed Saturday
    assert saturday.weekday() == 5
    slots = sched.generate_daily_slots(config, saturday)
    short_slots = [s for s in slots if s["content_kind"] == "SHORT"]
    # 8-hour window / 120-minute interval = 4 shorts, not 8
    assert len(short_slots) == 4


def test_annotate_slots_matches_nearby_scheduled_project():
    config = m.ScheduleConfig(full_videos_per_day=0, shorts_interval_minutes=60,
                               publishing_window_start="09:00", publishing_window_end="11:00")
    slots = sched.generate_daily_slots(config, dt.date(2026, 8, 13))
    fake_project = m.Project(
        id=1, title="Test", topic="Test", content_type=m.ContentType.SHORT,
        scheduled_publish_at=dt.datetime(2026, 8, 13, 9, 5),  # 5 min after the 09:00 slot
    )
    annotated = sched.annotate_slots_with_projects(slots, [fake_project])
    matched = [s for s in annotated if s["project"] is not None]
    assert len(matched) == 1
    assert matched[0]["project"]["id"] == 1


# ---------------------------------------------------------------------------
# Idea Vault
# ---------------------------------------------------------------------------

def test_idea_vault_create_list_and_promote(client):
    resp = client.post("/api/idea-vault", json={
        "topic": "New foldable phone rumor", "opportunity_score": 88,
        "content_type": "RUMOR_LEAK",
    })
    assert resp.status_code == 200
    idea_id = resp.json()["id"]
    assert resp.json()["status"] == "NEW"

    resp = client.get("/api/idea-vault")
    assert resp.status_code == 200
    assert any(i["id"] == idea_id for i in resp.json())

    resp = client.post(f"/api/idea-vault/{idea_id}/promote")
    assert resp.status_code == 200
    project = resp.json()
    assert project["topic"] == "New foldable phone rumor"
    assert project["content_type"] == "RUMOR_LEAK"

    resp = client.get("/api/idea-vault?status=QUEUED")
    assert any(i["id"] == idea_id for i in resp.json())


# ---------------------------------------------------------------------------
# Content Repurposing
# ---------------------------------------------------------------------------

def test_repurpose_requires_publish_stage_first(client):
    resp = client.post("/api/projects", json={
        "title": "No publish yet", "topic": "No publish yet", "content_type": "NEWS",
    })
    project_id = resp.json()["id"]
    resp = client.post(f"/api/projects/{project_id}/repurpose")
    assert resp.status_code == 400


def test_repurpose_creates_short_projects_from_shorts_ideas(client, monkeypatch, request):
    # monkeypatch.setenv auto-reverts the env var even if an assertion below
    # fails -- but the lru_cache on get_settings does NOT auto-clear when the
    # env var reverts, so that part needs an explicit finalizer (via the
    # built-in `request` fixture) to guarantee cleanup regardless of test
    # outcome (a manual cache_clear() at the end of the test would get
    # skipped by a failed assert, exactly the kind of leak that broke an
    # unrelated test in test_pipeline.py during development).
    from app.config import get_settings
    monkeypatch.setenv("FACT_CHECK_THRESHOLD", "10")
    get_settings.cache_clear()
    request.addfinalizer(get_settings.cache_clear)

    resp = client.post("/api/projects", json={
        "title": "Repurpose source", "topic": "Repurpose source topic",
        "content_type": "AI_NEWS", "pipeline_mode": "AUTO",
    })
    project_id = resp.json()["id"]
    client.post(f"/api/projects/{project_id}/run-full-pipeline")

    resp = client.post(f"/api/projects/{project_id}/repurpose")
    assert resp.status_code == 200
    shorts = resp.json()
    assert len(shorts) >= 1
    assert all(s["content_type"] == "SHORT" for s in shorts)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def test_search_finds_project_by_topic_substring(client):
    client.post("/api/projects", json={
        "title": "Searchable widget review", "topic": "Widget Pro Max review",
        "content_type": "PRODUCT_REVIEW",
    })
    resp = client.get("/api/search", params={"q": "Widget Pro"})
    assert resp.status_code == 200
    body = resp.json()
    assert any("Widget Pro" in p["topic"] for p in body["projects"])


def test_search_filters_by_content_type(client):
    client.post("/api/projects", json={"title": "A", "topic": "A", "content_type": "NEWS"})
    client.post("/api/projects", json={"title": "B", "topic": "B", "content_type": "SHORT"})
    resp = client.get("/api/search", params={"content_type": "SHORT"})
    body = resp.json()
    assert all(p["content_type"] == "SHORT" for p in body["projects"])
