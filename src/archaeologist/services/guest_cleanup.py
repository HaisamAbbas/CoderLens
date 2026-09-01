"""Periodic cleanup for ephemeral guest data (browse-public-repos-without-
login). A guest gets a real `User` row (`is_guest=True`) so every existing
per-user ownership/isolation mechanism works unmodified — this reaper is what
keeps that data from accumulating forever, since a guest never explicitly
signs up or deletes an account. Run from a background loop started in
main.py's lifespan, not on a request path.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from archaeologist.config import settings
from archaeologist.indexing.opensearch_client import get_client
from archaeologist.models.db import session_scope
from archaeologist.models.entities import IngestJob, Repo, User, UsageLedger
from archaeologist.services.repo_lifecycle import delete_repo


def reap_stale_guests(ttl_hours: int | None = None) -> int:
    """Delete every guest account (and everything it owns) whose last
    activity is older than `ttl_hours`. Returns how many were removed."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours or settings.guest_data_ttl_hours)
    with session_scope() as session:
        stale_ids = list(session.scalars(
            select(User.id).where(User.is_guest.is_(True), User.last_active_at < cutoff)
        ))
    if not stale_ids:
        return 0

    client = get_client()
    for user_id in stale_ids:
        with session_scope() as session:
            repo_ids = list(session.scalars(select(Repo.id).where(Repo.user_id == user_id)))
        for repo_id in repo_ids:
            with session_scope() as session:
                delete_repo(session, client, repo_id)
        with session_scope() as session:
            session.execute(delete(IngestJob).where(IngestJob.user_id == user_id))
            session.execute(delete(UsageLedger).where(UsageLedger.user_id == user_id))
            session.execute(delete(User).where(User.id == user_id))
    return len(stale_ids)
