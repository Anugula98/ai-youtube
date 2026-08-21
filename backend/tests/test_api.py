"""Tests hitting the FastAPI app directly (TestClient) rather than the
pipeline module — covers auth, serialization, and the HTTP-level contract."""
import os


def test_root_describes_backend_entrypoints(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["docs"] == "/docs"
    assert body["api"] == "/api"


def test_api_index_describes_resources(client):
    resp = client.get("/api")
    assert resp.status_code == 200
    body = resp.json()
    assert body["resources"]["projects"] == "/api/projects"
    assert body["resources"]["idea_vault"] == "/api/idea-vault"
    assert body["resources"]["youtube_config"] == "/api/youtube-config"


def test_healthz_does_not_require_auth(client):
    assert client.get("/healthz").status_code == 200


def test_readyz_confirms_db_connection(client):
    assert client.get("/readyz").status_code == 200


def test_youtube_config_hides_secrets_and_preserves_blank_updates(client):
    resp = client.get("/api/youtube-config")
    assert resp.status_code == 200
    assert resp.json()["has_client_secret"] is False

    resp = client.put("/api/youtube-config", json={
        "channel_id": "UC123",
        "channel_name": "Newsroom Test Channel",
        "default_privacy_status": "unlisted",
        "default_category_id": "28",
        "default_language": "en",
        "default_tags": ["ai", "technology"],
        "made_for_kids": False,
        "auto_publish_enabled": True,
        "upload_description_footer": "Subscribe for more.",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "refresh_token": "refresh-token",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["channel_id"] == "UC123"
    assert body["default_tags"] == ["ai", "technology"]
    assert body["has_client_id"] is True
    assert body["has_client_secret"] is True
    assert body["has_refresh_token"] is True
    assert "client_secret" not in body

    resp = client.put("/api/youtube-config", json={
        "channel_id": "UC456",
        "channel_name": "Newsroom Test Channel",
        "default_privacy_status": "private",
        "default_category_id": "28",
        "default_language": "en",
        "default_tags": [],
        "made_for_kids": False,
        "auto_publish_enabled": False,
        "upload_description_footer": None,
        "client_id": "",
        "client_secret": "",
        "refresh_token": "",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["channel_id"] == "UC456"
    assert body["has_client_id"] is True
    assert body["has_client_secret"] is True
    assert body["has_refresh_token"] is True


def test_create_and_fetch_project(client):
    resp = client.post("/api/projects", json={
        "title": "API test project",
        "topic": "API test topic",
        "content_type": "NEWS",
    })
    assert resp.status_code == 200
    project_id = resp.json()["id"]

    resp = client.get(f"/api/projects/{project_id}")
    assert resp.status_code == 200
    assert resp.json()["topic"] == "API test topic"


def test_missing_project_returns_404(client):
    resp = client.get("/api/projects/999999")
    assert resp.status_code == 404


def test_vertical_slice_endpoint_blocks_on_low_fact_check_score(client):
    resp = client.post("/api/projects", json={
        "title": "Slice test", "topic": "Slice test topic",
        "content_type": "NEWS", "pipeline_mode": "AUTO",
    })
    project_id = resp.json()["id"]

    resp = client.post(f"/api/projects/{project_id}/run-vertical-slice")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pipeline_state"] == "BLOCKED"
    assert body["current_stage"] == "RESEARCH"
    assert len(body["stage_runs"]) == 3  # AV, RESEARCH, FACT_CHECK all ran


def test_claims_endpoint_reflects_fact_check_results(client):
    resp = client.post("/api/projects", json={
        "title": "Claims test", "topic": "Claims test topic",
        "content_type": "NEWS", "pipeline_mode": "AUTO",
    })
    project_id = resp.json()["id"]
    client.post(f"/api/projects/{project_id}/run-vertical-slice")

    resp = client.get(f"/api/projects/{project_id}/claims")
    assert resp.status_code == 200
    claims = resp.json()
    assert len(claims) >= 1
    assert claims[0]["confidence"] == 55.0
