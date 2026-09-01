"""Session-based auth (Phase 1) + ephemeral guest access (browse public repos
with no account, added later).

Session cookie is a Starlette `SessionMiddleware` (itsdangerous-signed, set
up in main.py) holding either `{"user_id": int}` for a real GitHub-linked
account or `{"guest_user_id": int}` for a throwaway one — never both.

`get_current_user`/`CurrentUser` is used by almost every route and now NEVER
401s: a real signed-in session resolves to that account, and anyone else
gets (or keeps) an auto-created guest account. That's what lets the whole
app — ingest a public repo, browse it, ask/investigate, everything — work
with zero login. Routes that genuinely need a durable, GitHub-verified
identity (Confluence/Jira credentials, private-repo ingest) use
`RequireRealUser` instead, which keeps the old hard-401 behavior.
"""

import random
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request

from archaeologist.models.db import session_scope
from archaeologist.models.entities import User

_GUEST_ID_RETRIES = 5


def _create_guest(session, request: Request) -> User:
    # Negative github_id so it can never collide with a real (always
    # positive) GitHub account id, without loosening the column's
    # NOT NULL/unique constraint. Capped at 2**31-1: the column is a plain
    # Postgres INTEGER (32-bit signed), not BIGINT — a larger range
    # overflows it outright. A retry loop guards the still-unlikely case of
    # a random collision with another guest.
    last_exc: Exception | None = None
    for _ in range(_GUEST_ID_RETRIES):
        try:
            user = User(
                github_id=-random.randint(1, 2**31 - 1), github_login="guest",
                is_guest=True, last_active_at=datetime.now(timezone.utc),
            )
            session.add(user)
            session.flush()
            request.session["guest_user_id"] = user.id
            return user
        except Exception as exc:  # noqa: BLE001 - retry on the rare id collision
            session.rollback()
            last_exc = exc
    raise last_exc  # pragma: no cover - practically unreachable


def get_current_user(request: Request) -> User:
    """Never 401s — see module docstring."""
    with session_scope() as session:
        user_id = request.session.get("user_id")
        if user_id is not None:
            user = session.get(User, user_id)
            if user is not None:
                session.expunge(user)
                return user
            request.session.pop("user_id", None)  # stale — the row is gone

        guest_id = request.session.get("guest_user_id")
        user = session.get(User, guest_id) if guest_id is not None else None
        if user is None or not user.is_guest:
            user = _create_guest(session, request)
        else:
            user.last_active_at = datetime.now(timezone.utc)
        session.expunge(user)
        return user


CurrentUser = Depends(get_current_user)


def get_current_real_user(request: Request) -> User:
    """Hard-401s unless genuinely signed in with GitHub — for features that
    need a durable identity a guest fundamentally can't have (their own
    Confluence/Jira credentials, a private repo's access token)."""
    user_id = request.session.get("user_id")
    if user_id is None:
        raise HTTPException(401, "Sign in with GitHub to use this feature.")
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(401, "Sign in with GitHub to use this feature.")
        session.expunge(user)
        return user


RequireRealUser = Depends(get_current_real_user)
