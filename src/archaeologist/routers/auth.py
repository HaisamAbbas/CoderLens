"""GitHub OAuth login. Four routes: kick off the redirect, handle the
callback, log out, and report who's currently signed in."""

import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from archaeologist.auth import CurrentUser
from archaeologist.config import settings
from archaeologist.models.db import session_scope
from archaeologist.models.entities import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_USER_URL = "https://api.github.com/user"


def _callback_url(request: Request) -> str:
    """Must exactly match the callback URL registered on the GitHub OAuth
    App. Derived from the incoming request rather than hardcoded so the
    same code works for local dev and prod without a separate setting."""
    return str(request.url_for("github_callback"))


@router.get("/github/login")
def github_login(request: Request) -> RedirectResponse:
    if not settings.github_oauth_client_id:
        raise HTTPException(500, "GITHUB_OAUTH_CLIENT_ID is not set — sign-in is not configured.")
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    params = {
        "client_id": settings.github_oauth_client_id,
        "redirect_uri": _callback_url(request),
        "scope": "read:user user:email",
        "state": state,
    }
    return RedirectResponse(f"{_AUTHORIZE_URL}?{urlencode(params)}")


@router.get("/github/callback", name="github_callback")
def github_callback(request: Request, code: str, state: str) -> RedirectResponse:
    expected_state = request.session.pop("oauth_state", None)
    if not expected_state or state != expected_state:
        raise HTTPException(400, "OAuth state mismatch — please try signing in again.")

    with httpx.Client(timeout=15.0) as client:
        token_resp = client.post(
            _TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_oauth_client_id,
                "client_secret": settings.github_oauth_client_secret,
                "code": code,
                "redirect_uri": _callback_url(request),
            },
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(400, f"GitHub sign-in failed: {token_data.get('error_description', token_data)}")

        profile_resp = client.get(_USER_URL, headers={"Authorization": f"Bearer {access_token}"})
        profile_resp.raise_for_status()
        profile = profile_resp.json()

    with session_scope() as session:
        user = session.scalar(select(User).where(User.github_id == profile["id"]))
        if user is None:
            user = User(github_id=profile["id"])
            session.add(user)
        user.github_login = profile.get("login", "")
        user.email = profile.get("email")
        user.avatar_url = profile.get("avatar_url")
        session.flush()
        request.session["user_id"] = user.id

    return RedirectResponse(settings.frontend_base_url or "/")


@router.post("/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(user: User = CurrentUser) -> dict:
    return {
        "id": user.id,
        "github_login": user.github_login,
        "email": user.email,
        "avatar_url": user.avatar_url,
        "is_guest": user.is_guest,
    }
