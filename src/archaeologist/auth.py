"""Session-based auth (Phase 1 of the multi-user migration).

Session cookie is a Starlette `SessionMiddleware` (itsdangerous-signed, set
up in main.py) holding just `{"user_id": int}` — no server-side session
table. Every route that should require sign-in takes
`user: User = Depends(get_current_user)`.
"""

from fastapi import Depends, HTTPException, Request

from archaeologist.models.db import session_scope
from archaeologist.models.entities import User


def get_current_user(request: Request) -> User:
    user_id = request.session.get("user_id")
    if user_id is None:
        raise HTTPException(401, "Not signed in")
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None:
            # The session cookie outlived the user row (e.g. a dev DB reset) —
            # treat exactly like never having logged in, not a 500.
            raise HTTPException(401, "Not signed in")
        session.expunge(user)  # detach so it's safe to use after the session closes
        return user


CurrentUser = Depends(get_current_user)


def get_current_user_optional(request: Request) -> User | None:
    """Same lookup as get_current_user, but returns None instead of 401 —
    for routes that work logged out (e.g. /api/status's provider info) but
    also want to report per-user state when a session IS present."""
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None:
            return None
        session.expunge(user)
        return user


OptionalUser = Depends(get_current_user_optional)
