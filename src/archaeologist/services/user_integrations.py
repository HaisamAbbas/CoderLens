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
from archaeologist.security import decrypt, encrypt


def get(session: Session, user_id: int) -> UserIntegration | None:
    return session.scalar(select(UserIntegration).where(UserIntegration.user_id == user_id))


def confluence_configured(integ: UserIntegration | None) -> bool:
    return bool(integ and integ.confluence_base_url and integ.confluence_email
               and integ.confluence_api_token_encrypted and integ.confluence_space_key)


def jira_configured(integ: UserIntegration | None) -> bool:
    return bool(integ and integ.jira_base_url and integ.jira_email
               and integ.jira_api_token_encrypted and integ.jira_project_key)


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
    integ.confluence_base_url = base_url.strip()
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
    integ.jira_base_url = base_url.strip()
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
