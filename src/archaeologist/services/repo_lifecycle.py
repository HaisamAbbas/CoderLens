"""Fully remove one repo and everything under it. Built for the guest-data
reaper (services/guest_cleanup.py), but generic — nothing here assumes the
owner is a guest.

Also removes the on-disk git clone. The clone directory is namespaced per
user (repos/<user_id>/<owner>__<name>, see ingestion.repository.clone_or_open)
and (user_id, url) is a unique constraint on Repo, so this row's clone
directory is never shared with another Repo row — deleting it here can't
affect a different repo. Before that namespacing, this deliberately never
touched the clone (a shared owner__name directory could be a different
user's still-live repo); now that each user has their own, leaving it
behind would just accumulate disk forever with nothing left in the
database that references it (see services/guest_cleanup.py, the one caller
of this function today).
"""

import shutil
from pathlib import Path

from sqlalchemy import delete

from archaeologist.config import settings
from archaeologist.indexing import code_index, evidence_index
from archaeologist.models.entities import (
    Commit,
    CommitFile,
    ConfluencePublishJob,
    Conversation,
    File,
    Issue,
    JiraTicketJob,
    Repo,
    Symbol,
    SymbolEdge,
    Weakness,
    WeaknessScanJob,
)


def delete_repo(session, client, repo_id: int) -> None:
    """Delete order respects FK constraints (no ON DELETE CASCADE is set on
    any of these — see indexing/run.py's extract_to_postgres for the same
    SymbolEdge/Weakness-before-Symbol ordering this mirrors), then wipes this
    repo's OpenSearch documents. `client` may be None to skip the OpenSearch
    step (e.g. in tests without a live cluster)."""
    repo = session.get(Repo, repo_id)
    cloned_path = repo.cloned_path if repo else None

    session.execute(delete(SymbolEdge).where(SymbolEdge.repo_id == repo_id))
    session.execute(delete(Weakness).where(Weakness.repo_id == repo_id))
    session.execute(delete(Symbol).where(Symbol.repo_id == repo_id))
    session.execute(delete(ConfluencePublishJob).where(ConfluencePublishJob.repo_id == repo_id))
    session.execute(delete(WeaknessScanJob).where(WeaknessScanJob.repo_id == repo_id))
    session.execute(delete(JiraTicketJob).where(JiraTicketJob.repo_id == repo_id))
    session.execute(delete(Conversation).where(Conversation.repo_id == repo_id))
    session.execute(delete(CommitFile).where(CommitFile.repo_id == repo_id))
    session.execute(delete(Commit).where(Commit.repo_id == repo_id))
    session.execute(delete(Issue).where(Issue.repo_id == repo_id))
    session.execute(delete(File).where(File.repo_id == repo_id))
    session.execute(delete(Repo).where(Repo.id == repo_id))

    if client is not None:
        code_index.delete_repo_docs(client, repo_id)
        evidence_index.delete_repo_docs(client, repo_id)

    if cloned_path:
        _remove_clone(cloned_path)


def _remove_clone(cloned_path: str) -> None:
    """Best-effort delete, contained to repos_dir — a stale/pre-migration
    `cloned_path` (before per-user namespacing existed) could point at a
    directory still shared with another user's repo; only ever remove a
    path that resolves inside settings.repos_dir, and never let a disk
    error here fail the whole delete (the DB rows are already gone)."""
    try:
        repos_root = Path(settings.repos_dir).resolve()
        path = Path(cloned_path).resolve()
        if path.is_relative_to(repos_root):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:  # noqa: BLE001 - disk cleanup is best-effort
        pass
