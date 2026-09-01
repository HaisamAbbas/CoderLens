"""Background ingestion service.

Runs the full pipeline — clone → streams → symbols → code index → evidence index
→ dependency graph — as a tracked background job, so the web UI can offer
one-click "add a repository" with live progress.

Job status is persisted in Postgres (the `ingest_jobs` table), not kept in an
in-process dict — free-tier hosts restart the process on every redeploy and
on any OOM, which used to silently orphan an in-flight job and leave the UI
polling a job id that no longer existed anywhere.

Every job is owned by a user (Phase 2 of the multi-user migration) — IngestJob
has no repo_id (a Repo row doesn't exist yet when a job starts), so user_id is
the only ownership anchor available until the ingest completes.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

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
        "user_id": job.user_id,
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


def list_jobs(user_id: int) -> list[dict]:
    with session_scope() as session:
        jobs = session.scalars(
            select(IngestJob).where(IngestJob.user_id == user_id)
            .order_by(IngestJob.created_at.desc())
        ).all()
        return [_serialize(j) for j in jobs]


def running_job_for(url: str, user_id: int) -> dict | None:
    """Scoped by (url, user_id) — two different users each starting an
    ingest of the same URL at once must never see each other's job."""
    with session_scope() as session:
        job = session.scalar(
            select(IngestJob)
            .where(IngestJob.repo_url == url, IngestJob.user_id == user_id,
                   IngestJob.status == "running")
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


def start_ingest(repo_url: str, user_id: int, token: str = "") -> dict:
    """Start a full ingest in a background thread. Returns the job.

    If a job for the same repo is already running FOR THIS USER, returns it
    (idempotent) — a different user's in-flight job for the same URL is a
    separate job, not the same one.
    `token` is an optional GitHub PAT used transiently at clone time for
    private repos — deliberately NOT a persisted job field (no new column,
    never echoed back through any response).
    """
    existing = running_job_for(repo_url, user_id)
    if existing is not None:
        return existing

    job_id = uuid.uuid4().hex[:12]
    with session_scope() as session:
        session.add(IngestJob(id=job_id, user_id=user_id, repo_url=repo_url))

    thread = threading.Thread(target=_run, args=(job_id, repo_url, user_id, token), daemon=True)
    thread.start()
    return job_status(job_id)


def _run(job_id: str, repo_url: str, user_id: int, token: str = "") -> None:
    try:
        _pipeline(job_id, repo_url, user_id, token)
        _update(job_id, status="done", step="", message="Ingest complete")
    except Exception as exc:  # noqa: BLE001 - surface any failure on the job
        status = job_status(job_id)
        step = status["step"] if status else ""
        _update(job_id, status="error", step="", error=str(exc), message=f"Failed at step {step or 'start'}")


CLONE_TIMEOUT_S = 600  # generous for a legitimately large repo; a real hang (a
                       # stalled network call inside GitPython's clone_from,
                       # which has no timeout of its own) needs a hard ceiling
                       # or the job sits "running" forever with zero feedback.


def _pipeline(job_id: str, repo_url: str, user_id: int, token: str = "") -> None:
    # --- 1. Clone + walk the five streams into Postgres ---
    _update(job_id, step="clone", message=f"Cloning and walking {repo_url} …")
    # Web-triggered ingests cap git history at the 2,000 most recent commits:
    # commit.stats() runs a real diff per commit, and at express-scale (~8.5k
    # commits) an uncapped walk looks hung for 30+ minutes. 2,000 keeps the
    # churn/coupling/hotspot signals fully populated; `None` (all history)
    # remains available to the CLI caller.
    # NOT a `with` block on purpose: ThreadPoolExecutor.__exit__ calls
    # shutdown(wait=True), which would block on exactly the same stuck thread
    # future.result()'s timeout just gave up on — defeating the timeout
    # entirely. shutdown(wait=False) below lets this function return the
    # moment the timeout fires; the orphaned thread finishes or dies on its
    # own, unobserved (Python has no API to forcibly kill a thread).
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(ingest_repository, repo_url=repo_url, user_id=user_id,
                         token=token, max_commits=2000)
    try:
        stats = future.result(timeout=CLONE_TIMEOUT_S)
    except FutureTimeoutError as exc:
        pool.shutdown(wait=False)
        raise RuntimeError(
            f"Clone/history-walk exceeded {CLONE_TIMEOUT_S}s — likely a stalled "
            "network call to the git remote. Try again; if it keeps happening, "
            "the host's outbound network to that remote may be the issue."
        ) from exc
    pool.shutdown(wait=False)
    job_stats = {
        "files": stats.files, "commits": stats.commits,
        "issues": stats.issues, "prs": stats.prs,
    }
    _update(job_id, stats=job_stats)

    with session_scope() as session:
        # Scoped by (url, user_id) — Repo.url is no longer globally unique,
        # so an unscoped lookup could attach this job's later steps (symbol
        # extraction, indexing) to a DIFFERENT user's repo with the same URL.
        repo = session.scalar(
            select(Repo).where(Repo.url == repo_url, Repo.user_id == user_id)
            .order_by(Repo.id.desc())
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
