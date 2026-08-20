"""Phase 0 smoke tests — the app boots and liveness responds without any
external services running."""

from fastapi.testclient import TestClient

from archaeologist.main import app

client = TestClient(app)


def test_api_root():
    """The JSON API root is always served as JSON (unlike /, which becomes the
    SPA shell when frontend/dist exists)."""
    resp = client.get("/api")
    assert resp.status_code == 200
    assert resp.json()["name"] == "AI Codebase Archaeologist"


def test_spa_or_api_root():
    """/ returns 200 either way — the SPA shell when a build exists, otherwise
    the JSON API root."""
    resp = client.get("/")
    assert resp.status_code == 200
    if "application/json" in resp.headers.get("content-type", ""):
        assert resp.json()["name"] == "AI Codebase Archaeologist"


def test_health_liveness():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
