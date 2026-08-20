"""Background ingestion service.

Runs the full pipeline — clone → streams → symbols → code index → evidence index
→ dependency graph — as a tracked background job, so the web UI can offer
one-click "add a repository" with live progress.

Jobs live in an in-process dict (fine for a single-process dev server). If the
app is ever run multi-process, swap this for Redis — the job ids and the
`status`/`step`/`message` contract stay the same.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from archaeologist.indexing import graph as graph_builder
from archaeologist.indexing.run import extract_to_postgres, index_to_opensearch
from archaeologist.indexing.streams_run import build_evidence_index
from archaeologist.ingestion.pipeline import ingest_repository
from archaeologist.models.db import session_scope
from archaeologist.models.entities import Repo


@dataclass
class Job:
    id: str
    repo_url: str
    status: str = "running"  # running | done | error
    step: str = ""
    message: str = ""
    stats: dict = field(default_factory=dict)
    error: str = ""
    created_at: float = field(default_factory=time.time)


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def get_job(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def job_status(job_id: str) -> dict | None:
    job = get_job(job_id)
    return _serialize(job) if job is not None else None


def list_jobs() -> list[dict]:
    with _lock:
        jobs = sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)
        return [_serialize(j) for j in jobs]


def _serialize(job: Job) -> dict:
    return {
        "id": job.id,
        "repo_url": job.repo_url,
        "status": job.status,
        "step": job.step,
        "message": job.message,
        "stats": job.stats,
        "error": job.error,
    }


def running_job_for(url: str) -> Job | None:
    with _lock:
        for job in _jobs.values():
            if job.repo_url == url and job.status == "running":
                return job
    return None


def start_ingest(repo_url: str) -> Job:
    """Start a full ingest in a background thread. Returns the job.

    If a job for the same repo is already running, returns it (idempotent).
    """
    existing = running_job_for(repo_url)
    if existing is not None:
        return existing

    job = Job(id=uuid.uuid4().hex[:12], repo_url=repo_url)
    with _lock:
        _jobs[job.id] = job
    thread = threading.Thread(target=_run, args=(job,), daemon=True)
    thread.start()
    return job


def _run(job: Job) -> None:
    try:
        _pipeline(job)
        job.status = "done"
        job.step = ""
        job.message = "Ingest complete"
    except Exception as exc:  # noqa: BLE001 - surface any failure on the job
        job.status = "error"
        job.step = ""
        job.error = str(exc)
        job.message = f"Failed at step {job.step or 'start'}"


def _pipeline(job: Job) -> None:
    # --- 1. Clone + walk the five streams into Postgres ---
    job.step = "clone"
    job.message = f"Cloning and walking {job.repo_url} …"
    stats = ingest_repository(repo_url=job.repo_url)
    job.stats = {
        "files": stats.files, "commits": stats.commits,
        "issues": stats.issues, "prs": stats.prs,
    }

    with session_scope() as session:
        repo = session.scalar(
            select(Repo).where(Repo.url == job.repo_url).order_by(Repo.id.desc())
        )
        if repo is None:
            raise RuntimeError("Ingestion finished but no repo row was created")
        repo_id = repo.id

    # --- 2. Extract AST symbols + build the code index ---
    job.step = "symbols"
    job.message = "Extracting code symbols (tree-sitter) …"
    n_symbols = extract_to_postgres(repo_id=repo_id)
    job.stats["symbols"] = n_symbols

    job.step = "code-index"
    job.message = "Indexing symbols into OpenSearch (BM25 + vectors) …"
    index_to_opensearch(embed=True, repo_id=repo_id)

    # --- 3. Docs / commits / issues evidence index ---
    job.step = "evidence-index"
    job.message = "Indexing docs, commits and issues …"
    build_evidence_index(embed=True, repo_id=repo_id)

    # --- 4. Dependency graph (calls + inheritance) ---
    job.step = "graph"
    job.message = "Building the dependency graph …"
    graph_builder.build_graph(repo_id=repo_id)
