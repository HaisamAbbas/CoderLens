"""Smoke tests for the product API additions — repo management and job status.

These only exercise paths that need no database, OpenSearch, or network:
URL validation happens before any ingest starts, and job lookups hit the
in-process job store. Every route now requires a signed-in user (Phase 2 of
the multi-user migration), so a fake user is injected via FastAPI's
dependency_overrides rather than a real session cookie/login.
"""

from fastapi.testclient import TestClient

from archaeologist.auth import get_current_user
from archaeologist.main import app
from archaeologist.models.entities import User

app.dependency_overrides[get_current_user] = lambda: User(id=1, github_id=1, github_login="test-user")

client = TestClient(app)


def teardown_module() -> None:
    """`app` is a shared singleton imported by every test file — an override
    left in place would silently affect any other test hitting these routes."""
    app.dependency_overrides.pop(get_current_user, None)


def test_add_repo_rejects_bad_url():
    resp = client.post("/api/repos", json={"url": "not-a-url"})
    assert resp.status_code == 422


def test_add_repo_requires_url():
    resp = client.post("/api/repos", json={})
    assert resp.status_code == 422


def test_unknown_job_is_404():
    resp = client.get("/api/repos/jobs/does-not-exist")
    assert resp.status_code == 404
