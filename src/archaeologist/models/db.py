"""Database engine / session helpers.

    from archaeologist.models.db import init_db, session_scope
    init_db()
    with session_scope() as session:
        ...
"""

from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from archaeologist.config import settings
from archaeologist.models.base import Base

engine = create_engine(settings.postgres_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Create all tables. Import entities so they register on Base.metadata."""
    from archaeologist.models import entities  # noqa: F401

    Base.metadata.create_all(engine)
    _ensure_additive_columns()


def _ensure_additive_columns() -> None:
    """No Alembic in this project (a deliberate, known limitation) —
    create_all() only creates missing TABLES, never alters existing ones, so
    a new column on an already-existing table (e.g. User.is_guest for guest
    sessions) silently wouldn't exist in a real database otherwise. This is a
    minimal, idempotent patch for exactly that case: additive, backfillable
    columns only. A genuinely destructive/renaming change still needs the
    "reset the local dev schema" approach TRACKING.md documents from Phase 2."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    if "users" in table_names:
        existing = {c["name"] for c in inspector.get_columns("users")}
        with engine.begin() as conn:
            if "is_guest" not in existing:
                conn.execute(text("ALTER TABLE users ADD COLUMN is_guest boolean NOT NULL DEFAULT false"))
            if "last_active_at" not in existing:
                conn.execute(text("ALTER TABLE users ADD COLUMN last_active_at timestamptz"))

    if "user_integrations" in table_names:
        existing = {c["name"] for c in inspector.get_columns("user_integrations")}
        if "github_pat_encrypted" not in existing:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE user_integrations ADD COLUMN github_pat_encrypted text NOT NULL DEFAULT ''"
                ))

    # repos.user_id / ingest_jobs.user_id: added by Phase 2 of the multi-user
    # migration (per-user repo ownership). A database created before that
    # phase never got these — create_all() only creates missing TABLES, so an
    # already-existing repos/ingest_jobs table silently kept its old
    # single-tenant shape, and every query that filters by user_id (which is
    # now all of them) crashes with UndefinedColumn instead of just returning
    # nothing. Nullable, not backfilled: there is no correct user to retroactively
    # attach a pre-migration row to, so it's left orphaned (invisible to every
    # user's queries, same as if it didn't exist) rather than guessed at.
    if "repos" in table_names:
        existing = {c["name"] for c in inspector.get_columns("repos")}
        if "user_id" not in existing:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE repos ADD COLUMN user_id integer REFERENCES users(id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_repos_user_id ON repos (user_id)"))

    if "ingest_jobs" in table_names:
        existing = {c["name"] for c in inspector.get_columns("ingest_jobs")}
        if "user_id" not in existing:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE ingest_jobs ADD COLUMN user_id integer REFERENCES users(id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ingest_jobs_user_id ON ingest_jobs (user_id)"))


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session: commit on success, rollback on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
