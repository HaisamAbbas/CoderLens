"""Background Jira-ticket service — mirrors `services/confluence_job.py`'s
persisted-job shape (fan-out of independent, separately-failable external
POSTs, results appended live), with the wrinkle Confluence never needed:
after each successful ticket POST, the matching Weakness row must flip to
status="ticketed" with its jira_url — a short session_scope() per item, the
same _update() mechanism, just touching a second table.

One failed POST sits in results as that item's error; it never sinks the rest
of the batch (re-running covers it — no auto-retry by design).
"""

import threading
import uuid

from sqlalchemy import select

from archaeologist.models.db import session_scope
from archaeologist.models.entities import JiraTicketJob, Repo, Weakness
from archaeologist.services import jira_client, user_integrations


def _serialize(job: JiraTicketJob) -> dict:
    return {
        "id": job.id,
        "repo_id": job.repo_id,
        "status": job.status,
        "finding_ids": job.finding_ids,
        "results": job.results or [],
        "error": job.error,
    }


def job_status(job_id: str) -> dict | None:
    with session_scope() as session:
        job = session.get(JiraTicketJob, job_id)
        return _serialize(job) if job is not None else None


def running_job_for(repo_id: int) -> dict | None:
    with session_scope() as session:
        job = session.scalar(
            select(JiraTicketJob)
            .where(JiraTicketJob.repo_id == repo_id,
                   JiraTicketJob.status == "running")
            .order_by(JiraTicketJob.created_at.desc())
        )
        return _serialize(job) if job is not None else None


def _update(job_id: str, **fields) -> None:
    with session_scope() as session:
        job = session.get(JiraTicketJob, job_id)
        if job is None:
            return
        for k, v in fields.items():
            setattr(job, k, v)


def start_tickets(repo_id: int, finding_ids: list[int]) -> dict:
    """Start a ticket batch in a background thread. Returns the job.

    If a batch for this repo is already running, returns it (idempotent).
    """
    existing = running_job_for(repo_id)
    if existing is not None:
        return existing

    job_id = uuid.uuid4().hex[:12]
    with session_scope() as session:
        session.add(JiraTicketJob(id=job_id, repo_id=repo_id, finding_ids=finding_ids))

    thread = threading.Thread(target=_run, args=(job_id, repo_id, list(finding_ids)), daemon=True)
    thread.start()
    return job_status(job_id)


def _run(job_id: str, repo_id: int, finding_ids: list[int]) -> None:
    try:
        _ticket(job_id, repo_id, finding_ids)
        _update(job_id, status="done")
    except Exception as exc:  # noqa: BLE001 - surface any failure on the job
        _update(job_id, status="error", error=str(exc))


def _ticket(job_id: str, repo_id: int, finding_ids: list[int]) -> None:
    with session_scope() as session:
        repo = session.get(Repo, repo_id)
        if repo is None:
            raise RuntimeError("Unknown repository for ticket job")
        integ = user_integrations.get(session, repo.user_id)
        if not user_integrations.jira_configured(integ):
            raise RuntimeError("Jira is not connected — set it up in Settings.")
        credentials = user_integrations.jira_credentials(integ)

    def on_progress(results_so_far: list[dict]) -> None:
        _update(job_id, results=results_so_far)

    outcome = _create_batch(repo_id, finding_ids, credentials, on_progress)
    _update(job_id, results=outcome)


def _create_batch(repo_id: int, finding_ids: list[int], credentials: dict, on_progress) -> list[dict]:
    """One external POST + one Weakness write-back per finding, each guarded —
    an invalid issue type or a network blip fails exactly its own item."""
    project_key, issue_type = credentials["project_key"], credentials["issue_type"]
    results: list[dict] = []
    with jira_client.open_client(
        credentials["base_url"], credentials["email"], credentials["api_token"]
    ) as client:
        for fid in finding_ids:
            result = _ticket_one(client, repo_id, fid, project_key, issue_type)
            results.append(result)
            if on_progress is not None:
                on_progress(list(results))
    return results


def _ticket_one(client, repo_id: int, fid: int, project_key: str, issue_type: str) -> dict:
    result: dict = {"finding_id": fid}
    try:
        # Short scope 1 — read the finding and release.
        with session_scope() as session:
            w = session.get(Weakness, fid)
            if w is None or w.repo_id != repo_id:
                result.update(status="error", error=f"Unknown finding #{fid} for this repo.")
                return result
            if w.status != "new":
                result.update(status="error", error=(
                    f"Finding #{fid} is '{w.status}', "
                    "only 'new' findings can be ticketed."))
                return result
            finding = {
                "severity": w.severity, "title": w.title, "description": w.description,
                "file_path": w.file_path, "start_line": w.start_line,
                "end_line": w.end_line, "suggested_fix": w.suggested_fix,
                "category": w.category,
            }

        issue = jira_client.create_issue(client, project_key, issue_type, finding)

        # Short scope 2 — write back only on success.
        with session_scope() as session:
            w = session.get(Weakness, fid)
            if w is not None:
                w.status = "ticketed"
                w.jira_url = issue["url"]
        result.update(status="ok", url=issue["url"], key=issue["key"])
    except Exception as exc:  # noqa: BLE001 - degrade per finding, keep the batch
        result.update(status="error", error=str(exc))
    return result
