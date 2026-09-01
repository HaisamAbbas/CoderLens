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
    if "users" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("users")}
    with engine.begin() as conn:
        if "is_guest" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_guest boolean NOT NULL DEFAULT false"))
        if "last_active_at" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN last_active_at timestamptz"))


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
