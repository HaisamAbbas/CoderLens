"""Smoke tests for the product API additions — repo management and job status.

These only exercise paths that need no database, OpenSearch, or network:
URL validation happens before any ingest starts, and job lookups hit the
in-process job store.
"""

from fastapi.testclient import TestClient

from archaeologist.main import app

client = TestClient(app)


def test_add_repo_rejects_bad_url():
    resp = client.post("/api/repos", json={"url": "not-a-url"})
    assert resp.status_code == 422


def test_add_repo_requires_url():
    resp = client.post("/api/repos", json={})
    assert resp.status_code == 422


def test_unknown_job_is_404():
    resp = client.get("/api/repos/jobs/does-not-exist")
    assert resp.status_code == 404
