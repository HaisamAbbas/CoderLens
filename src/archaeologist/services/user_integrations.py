"""Per-user Confluence/Jira credential storage (Phase 4 of the multi-user
migration) — replaces the old global CONFLUENCE_*/JIRA_* settings. One row
per user; API tokens are encrypted at rest (see security.py) and a blank
token on an update means "keep the existing one," never "clear it," so the
frontend never has to re-send (or even see) a saved token to update the
other fields.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from archaeologist.models.entities import UserIntegration
from archaeologist.net_guard import assert_public_https_url
from archaeologist.security import decrypt, encrypt


def _safe_base_url(raw: str) -> str:
    """Reject anything that isn't a real, public https:// URL before it's
    stored. Without this, a user could point their Confluence/Jira base URL
    at the cloud metadata endpoint, at localhost (this app's own Postgres/
    OpenSearch), or at an internal service — every publish/ticket job then
    makes an authenticated, credentialed server-side request to whatever
    was saved, and the resulting error text (surfaced through the job-status
    endpoints) turns that into a readable probe of internal network/DNS.
    http:// is also rejected outright: it would send the user's own Basic-
    auth Confluence/Jira token in cleartext to whatever host is configured."""
    try:
        return assert_public_https_url(raw)
    except ValueError as exc:
        raise ValueError(f"Base URL must be a public https:// URL ({exc})") from exc


def get(session: Session, user_id: int) -> UserIntegration | None:
    return session.scalar(select(UserIntegration).where(UserIntegration.user_id == user_id))


def confluence_configured(integ: UserIntegration | None) -> bool:
    return bool(integ and integ.confluence_base_url and integ.confluence_email
               and integ.confluence_api_token_encrypted and integ.confluence_space_key)


def jira_configured(integ: UserIntegration | None) -> bool:
    return bool(integ and integ.jira_base_url and integ.jira_email
               and integ.jira_api_token_encrypted and integ.jira_project_key)


def github_pat_configured(integ: UserIntegration | None) -> bool:
    return bool(integ and integ.github_pat_encrypted)


def confluence_credentials(integ: UserIntegration) -> dict:
    """Decrypted, ready to use — call only after confluence_configured() is True."""
    return {
        "base_url": integ.confluence_base_url,
        "email": integ.confluence_email,
        "api_token": decrypt(integ.confluence_api_token_encrypted),
        "space_key": integ.confluence_space_key,
    }


def jira_credentials(integ: UserIntegration) -> dict:
    """Decrypted, ready to use — call only after jira_configured() is True."""
    return {
        "base_url": integ.jira_base_url,
        "email": integ.jira_email,
        "api_token": decrypt(integ.jira_api_token_encrypted),
        "project_key": integ.jira_project_key,
        "issue_type": integ.jira_issue_type or "Task",
    }


def github_pat(integ: UserIntegration) -> str:
    """Decrypted, ready to use — call only after github_pat_configured() is True."""
    return decrypt(integ.github_pat_encrypted)


def _get_or_create(session: Session, user_id: int) -> UserIntegration:
    integ = get(session, user_id)
    if integ is None:
        integ = UserIntegration(user_id=user_id)
        session.add(integ)
    return integ


def upsert_confluence(
    session: Session, user_id: int, base_url: str, email: str, api_token: str, space_key: str,
) -> UserIntegration:
    integ = _get_or_create(session, user_id)
    integ.confluence_base_url = _safe_base_url(base_url)
    integ.confluence_email = email.strip()
    integ.confluence_space_key = space_key.strip()
    if api_token:  # blank = keep the existing encrypted token
        integ.confluence_api_token_encrypted = encrypt(api_token)
    return integ


def upsert_jira(
    session: Session, user_id: int, base_url: str, email: str, api_token: str,
    project_key: str, issue_type: str,
) -> UserIntegration:
    integ = _get_or_create(session, user_id)
    integ.jira_base_url = _safe_base_url(base_url)
    integ.jira_email = email.strip()
    integ.jira_project_key = project_key.strip()
    integ.jira_issue_type = issue_type.strip() or "Task"
    if api_token:  # blank = keep the existing encrypted token
        integ.jira_api_token_encrypted = encrypt(api_token)
    return integ


def clear_confluence(session: Session, user_id: int) -> None:
    integ = get(session, user_id)
    if integ is None:
        return
    integ.confluence_base_url = ""
    integ.confluence_email = ""
    integ.confluence_api_token_encrypted = ""
    integ.confluence_space_key = ""


def clear_jira(session: Session, user_id: int) -> None:
    integ = get(session, user_id)
    if integ is None:
        return
    integ.jira_base_url = ""
    integ.jira_email = ""
    integ.jira_api_token_encrypted = ""
    integ.jira_project_key = ""
    integ.jira_issue_type = "Task"


def upsert_github_pat(session: Session, user_id: int, api_token: str) -> UserIntegration:
    integ = _get_or_create(session, user_id)
    if api_token:  # blank on this route means "clear" (see routers/integrations.py) —
        integ.github_pat_encrypted = encrypt(api_token)  # a real value always overwrites
    return integ


def clear_github_pat(session: Session, user_id: int) -> None:
    integ = get(session, user_id)
    if integ is None:
        return
    integ.github_pat_encrypted = ""
