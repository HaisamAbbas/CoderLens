"""Background wiki-publish service — a line-for-line mirror of
`services/ingest.py`'s persisted-job shape (start / status / _run / _update),
but for Confluence publishing. Reads the repo's already-cached wiki (no
rebuild) and publishes the user-approved sections, appending each finished
section to `results` live so the UI can show per-section progress.
"""

import threading
import uuid

from sqlalchemy import select

from archaeologist.ingestion.repository import repo_slug
from archaeologist.models.db import session_scope
from archaeologist.models.entities import ConfluencePublishJob, Repo
from archaeologist.services import confluence_publish


def _serialize(job: ConfluencePublishJob) -> dict:
    return {
        "id": job.id,
        "repo_id": job.repo_id,
        "status": job.status,
        "section_keys": job.section_keys,
        "parent_url": job.parent_url,
        "results": job.results or [],
        "error": job.error,
    }


def job_status(job_id: str) -> dict | None:
    with session_scope() as session:
        job = session.get(ConfluencePublishJob, job_id)
        return _serialize(job) if job is not None else None


def running_job_for(repo_id: int) -> dict | None:
    with session_scope() as session:
        job = session.scalar(
            select(ConfluencePublishJob)
            .where(ConfluencePublishJob.repo_id == repo_id,
                   ConfluencePublishJob.status == "running")
            .order_by(ConfluencePublishJob.created_at.desc())
        )
        return _serialize(job) if job is not None else None


def _update(job_id: str, **fields) -> None:
    with session_scope() as session:
        job = session.get(ConfluencePublishJob, job_id)
        if job is None:
            return
        for k, v in fields.items():
            setattr(job, k, v)


def start_publish(repo_id: int, section_keys: list[str]) -> dict:
    """Start a publish in a background thread. Returns the job.

    If a publish for the same repo is already running, returns it (idempotent).
    """
    existing = running_job_for(repo_id)
    if existing is not None:
        return existing

    job_id = uuid.uuid4().hex[:12]
    with session_scope() as session:
        session.add(ConfluencePublishJob(
            id=job_id, repo_id=repo_id, section_keys=section_keys,
        ))

    thread = threading.Thread(
        target=_run, args=(job_id, repo_id, section_keys), daemon=True
    )
    thread.start()
    return job_status(job_id)


def _run(job_id: str, repo_id: int, section_keys: list[str]) -> None:
    try:
        _publish(job_id, repo_id, section_keys)
        _update(job_id, status="done")
    except Exception as exc:  # noqa: BLE001 - surface any failure on the job
        _update(job_id, status="error", error=str(exc))


def _publish(job_id: str, repo_id: int, section_keys: list[str]) -> None:
    with session_scope() as session:
        r = session.get(Repo, repo_id)
        if r is None:
            raise RuntimeError("Unknown repository for publish job")
        if not r.wiki_cache:
            raise RuntimeError("No cached wiki — generate it (visit Start Here) first.")
        # owner/name (not Repo.name, which is the bare name only) so two repos
        # with the same bare name can't collide on Confluence page titles.
        owner, name = repo_slug(r.url)
        label = f"{owner}/{name}"
        wiki = r.wiki_cache

    def on_progress(results_so_far: list[dict]) -> None:
        _update(job_id, results=results_so_far)

    outcome = confluence_publish.publish_wiki(
        label, wiki, section_keys, on_progress=on_progress
    )
    _update(job_id, parent_url=outcome["parent_url"], results=outcome["results"])
