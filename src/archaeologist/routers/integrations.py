"""/api/integrations — a user's own Confluence/Jira/GitHub connection
settings (Confluence/Jira: Phase 4 of the multi-user migration; GitHub: the
saved-PAT convenience so a signed-in user doesn't have to paste their token
every time they ingest a new private repo). Never returns a saved API token
back to the client; a blank token on a PUT means "keep the existing one."
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from archaeologist.auth import RequireRealUser
from archaeologist.models.db import session_scope
from archaeologist.models.entities import User
from archaeologist.services import user_integrations

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


@router.get("")
def get_integrations(user: User = RequireRealUser) -> dict:
    with session_scope() as s:
        integ = user_integrations.get(s, user.id)
        return {
            "confluence": {
                "configured": user_integrations.confluence_configured(integ),
                "base_url": integ.confluence_base_url if integ else "",
                "email": integ.confluence_email if integ else "",
                "space_key": integ.confluence_space_key if integ else "",
                "has_token": bool(integ and integ.confluence_api_token_encrypted),
            },
            "jira": {
                "configured": user_integrations.jira_configured(integ),
                "base_url": integ.jira_base_url if integ else "",
                "email": integ.jira_email if integ else "",
                "project_key": integ.jira_project_key if integ else "",
                "issue_type": integ.jira_issue_type if integ else "Task",
                "has_token": bool(integ and integ.jira_api_token_encrypted),
            },
            "github": {
                "configured": user_integrations.github_pat_configured(integ),
                "has_token": bool(integ and integ.github_pat_encrypted),
            },
        }


class ConfluenceIntegrationBody(BaseModel):
    base_url: str
    email: str
    api_token: str = ""  # blank = keep the existing saved token
    space_key: str


@router.put("/confluence")
def put_confluence(body: ConfluenceIntegrationBody, user: User = RequireRealUser) -> dict:
    try:
        with session_scope() as s:
            user_integrations.upsert_confluence(
                s, user.id, body.base_url, body.email, body.api_token, body.space_key,
            )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True}


@router.delete("/confluence")
def delete_confluence(user: User = RequireRealUser) -> dict:
    with session_scope() as s:
        user_integrations.clear_confluence(s, user.id)
    return {"ok": True}


class JiraIntegrationBody(BaseModel):
    base_url: str
    email: str
    api_token: str = ""  # blank = keep the existing saved token
    project_key: str
    issue_type: str = "Task"


@router.put("/jira")
def put_jira(body: JiraIntegrationBody, user: User = RequireRealUser) -> dict:
    try:
        with session_scope() as s:
            user_integrations.upsert_jira(
                s, user.id, body.base_url, body.email, body.api_token,
                body.project_key, body.issue_type,
            )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True}


@router.delete("/jira")
def delete_jira(user: User = RequireRealUser) -> dict:
    with session_scope() as s:
        user_integrations.clear_jira(s, user.id)
    return {"ok": True}


class GithubIntegrationBody(BaseModel):
    api_token: str


@router.put("/github")
def put_github(body: GithubIntegrationBody, user: User = RequireRealUser) -> dict:
    with session_scope() as s:
        user_integrations.upsert_github_pat(s, user.id, body.api_token)
    return {"ok": True}


@router.delete("/github")
def delete_github(user: User = RequireRealUser) -> dict:
    with session_scope() as s:
        user_integrations.clear_github_pat(s, user.id)
    return {"ok": True}
