"""Background weakness-scan service — mirrors `services/ingest.py`'s persisted
job shape (start / status / _run / _update): one internal pipeline with
discrete progress, no third-party writes, status durable across restarts.

Runs analysis/weaknesses.scan_repo (LLM fan-out), persists findings via
replace_findings, and records the cap/truncation disclosures on the job so the
UI can show them instead of silently showing partial coverage.
"""

import threading
import uuid

from sqlalchemy import select

from archaeologist.analysis import weaknesses
from archaeologist.models.db import session_scope
from archaeologist.models.entities import Repo, WeaknessScanJob


def _serialize(job: WeaknessScanJob) -> dict:
    return {
        "id": job.id,
        "repo_id": job.repo_id,
        "status": job.status,
        "files_scanned": job.files_scanned,
        "files_total": job.files_total,
        "message": job.message,
        "notes": job.notes or [],
        "error": job.error,
    }


def job_status(job_id: str) -> dict | None:
    with session_scope() as session:
        job = session.get(WeaknessScanJob, job_id)
        return _serialize(job) if job is not None else None


def running_job_for(repo_id: int) -> dict | None:
    with session_scope() as session:
        job = session.scalar(
            select(WeaknessScanJob)
            .where(WeaknessScanJob.repo_id == repo_id,
                   WeaknessScanJob.status == "running")
            .order_by(WeaknessScanJob.created_at.desc())
        )
        return _serialize(job) if job is not None else None


def latest_job_for(repo_id: int) -> dict | None:
    with session_scope() as session:
        job = session.scalar(
            select(WeaknessScanJob)
            .where(WeaknessScanJob.repo_id == repo_id,
                   WeaknessScanJob.status != "running")
            .order_by(WeaknessScanJob.created_at.desc())
        )
        return _serialize(job) if job is not None else None


def _update(job_id: str, **fields) -> None:
    with session_scope() as session:
        job = session.get(WeaknessScanJob, job_id)
        if job is None:
            return
        for k, v in fields.items():
            setattr(job, k, v)


def start_scan(repo_id: int, scan_all: bool = False) -> dict:
    """Start a scan in a background thread. Returns the job.

    If a scan for this repo is already running, returns it (idempotent).
    """
    existing = running_job_for(repo_id)
    if existing is not None:
        return existing

    job_id = uuid.uuid4().hex[:12]
    with session_scope() as session:
        session.add(WeaknessScanJob(id=job_id, repo_id=repo_id))

    thread = threading.Thread(target=_run, args=(job_id, repo_id, scan_all), daemon=True)
    thread.start()
    return job_status(job_id)


def _run(job_id: str, repo_id: int, scan_all: bool) -> None:
    try:
        _scan(job_id, repo_id, scan_all)
        _update(job_id, status="done", message="Scan complete")
    except Exception as exc:  # noqa: BLE001 - surface any failure on the job
        _update(job_id, status="error", error=str(exc))


def _scan(job_id: str, repo_id: int, scan_all: bool) -> None:
    def on_progress(done: int, total: int) -> None:
        _update(job_id, files_scanned=done, files_total=total)

    # Scope 1 — select (fast query, released before the slow LLM sweep).
    with session_scope() as session:
        repo = session.get(Repo, repo_id)
        if repo is None:
            raise RuntimeError("Unknown repository for scan job")
        head_sha = repo.head_sha
        files, total_code = weaknesses.select_files(session, repo_id,
                                                    weaknesses.MAX_FILES, scan_all)

    # Scope 2 — LLM fan-out with no DB transaction pinned.
    findings, notes = weaknesses.scan_files(files, total_code, scan_all, on_progress)

    # Scope 3 — persist inside the same short transaction that replaces
    # prior new/dismissed rows.
    with session_scope() as session:
        weaknesses.replace_findings(session, repo_id, head_sha, findings)

    _update(
        job_id,
        notes=notes,
        message=f"{len(findings)} finding(s) from {len(files)} scanned file(s)",
    )
