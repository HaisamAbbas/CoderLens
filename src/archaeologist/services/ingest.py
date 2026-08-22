"""Background ingestion service.

Runs the full pipeline — clone → streams → symbols → code index → evidence index
→ dependency graph — as a tracked background job, so the web UI can offer
one-click "add a repository" with live progress.

Job status is persisted in Postgres (the `ingest_jobs` table), not kept in an
in-process dict — free-tier hosts restart the process on every redeploy and
on any OOM, which used to silently orphan an in-flight job and leave the UI
polling a job id that no longer existed anywhere.
"""

import threading
import uuid

from sqlalchemy import select

from archaeologist.indexing import graph as graph_builder
from archaeologist.indexing.run import extract_to_postgres, index_to_opensearch
from archaeologist.indexing.streams_run import build_evidence_index
from archaeologist.ingestion.pipeline import ingest_repository
from archaeologist.models.db import session_scope
from archaeologist.models.entities import IngestJob, Repo


def _serialize(job: IngestJob) -> dict:
    return {
        "id": job.id,
        "repo_url": job.repo_url,
        "status": job.status,
        "step": job.step,
        "message": job.message,
        "stats": job.stats,
        "error": job.error,
    }


def job_status(job_id: str) -> dict | None:
    with session_scope() as session:
        job = session.get(IngestJob, job_id)
        return _serialize(job) if job is not None else None


def list_jobs() -> list[dict]:
    with session_scope() as session:
        jobs = session.scalars(
            select(IngestJob).order_by(IngestJob.created_at.desc())
        ).all()
        return [_serialize(j) for j in jobs]


def running_job_for(url: str) -> dict | None:
    with session_scope() as session:
        job = session.scalar(
            select(IngestJob)
            .where(IngestJob.repo_url == url, IngestJob.status == "running")
            .order_by(IngestJob.created_at.desc())
        )
        return _serialize(job) if job is not None else None


def _update(job_id: str, **fields) -> None:
    with session_scope() as session:
        job = session.get(IngestJob, job_id)
        if job is None:
            return
        for k, v in fields.items():
            setattr(job, k, v)


def start_ingest(repo_url: str, token: str = "") -> dict:
    """Start a full ingest in a background thread. Returns the job.

    If a job for the same repo is already running, returns it (idempotent).
    `token` is an optional GitHub PAT used transiently at clone time for
    private repos — deliberately NOT a persisted job field (no new column,
    never echoed back through any response).
    """
    existing = running_job_for(repo_url)
    if existing is not None:
        return existing

    job_id = uuid.uuid4().hex[:12]
    with session_scope() as session:
        session.add(IngestJob(id=job_id, repo_url=repo_url))

    thread = threading.Thread(target=_run, args=(job_id, repo_url, token), daemon=True)
    thread.start()
    return job_status(job_id)


def _run(job_id: str, repo_url: str, token: str = "") -> None:
    try:
        _pipeline(job_id, repo_url, token)
        _update(job_id, status="done", step="", message="Ingest complete")
    except Exception as exc:  # noqa: BLE001 - surface any failure on the job
        status = job_status(job_id)
        step = status["step"] if status else ""
        _update(job_id, status="error", step="", error=str(exc), message=f"Failed at step {step or 'start'}")


def _pipeline(job_id: str, repo_url: str, token: str = "") -> None:
    # --- 1. Clone + walk the five streams into Postgres ---
    _update(job_id, step="clone", message=f"Cloning and walking {repo_url} …")
    stats = ingest_repository(repo_url=repo_url, token=token)
    job_stats = {
        "files": stats.files, "commits": stats.commits,
        "issues": stats.issues, "prs": stats.prs,
    }
    _update(job_id, stats=job_stats)

    with session_scope() as session:
        repo = session.scalar(
            select(Repo).where(Repo.url == repo_url).order_by(Repo.id.desc())
        )
        if repo is None:
            raise RuntimeError("Ingestion finished but no repo row was created")
        repo_id = repo.id

    # --- 2. Extract AST symbols + build the code index ---
    _update(job_id, step="symbols", message="Extracting code symbols (tree-sitter) …")
    n_symbols = extract_to_postgres(repo_id=repo_id)
    job_stats["symbols"] = n_symbols
    _update(job_id, stats=job_stats)

    _update(job_id, step="code-index", message="Indexing symbols into OpenSearch (BM25 + vectors) …")
    index_to_opensearch(embed=True, repo_id=repo_id)

    # --- 3. Docs / commits / issues evidence index ---
    _update(job_id, step="evidence-index", message="Indexing docs, commits and issues …")
    build_evidence_index(embed=True, repo_id=repo_id)

    # --- 4. Dependency graph (calls + inheritance) ---
    _update(job_id, step="graph", message="Building the dependency graph …")
    graph_builder.build_graph(repo_id=repo_id)
