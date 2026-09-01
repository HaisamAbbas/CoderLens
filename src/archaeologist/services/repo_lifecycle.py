"""Fully remove one repo and everything under it. Built for the guest-data
reaper (services/guest_cleanup.py), but generic — nothing here assumes the
owner is a guest.

Does NOT touch the on-disk git clone (repos/<owner>__<name>):
`ingestion.repository.clone_or_open` reuses that directory across every
`Repo` row that shares the same URL (a known, pre-existing limitation — see
TRACKING.md), so deleting it here could break a different user's still-live
repo pointed at the same clone.
"""

from sqlalchemy import delete

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
