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
