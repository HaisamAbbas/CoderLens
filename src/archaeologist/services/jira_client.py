"""Thin sync REST wrapper over the Jira Cloud v3 issue API.

Mirrors `services/confluence_client.py`: small httpx client with plain
functions and a clear RuntimeError on auth failures. Same Basic(email, API
token) scheme as Confluence — but settings fields are independent, so each
feature must be configured on its own.

Two Jira-specific wrinkles:
- base_url has NO /wiki path segment (unlike Confluence's).
- Descriptions must be Atlassian Document Format (ADF), not HTML/markdown.
"""

import httpx

from archaeologist.config import settings


def open_client() -> httpx.Client:
    s = settings
    return httpx.Client(
        auth=(s.jira_email, s.jira_api_token),
        base_url=s.jira_base_url,
        timeout=30.0,
    )


def _check(resp: httpx.Response) -> None:
    if resp.status_code in (401, 403):
        raise RuntimeError(
            f"Jira rejected the credentials ({resp.status_code}). "
            "Check JIRA_EMAIL / JIRA_API_TOKEN and that the token has not expired."
        )
    if resp.status_code == 404:
        raise RuntimeError(
            "Jira returned 404 — check JIRA_BASE_URL (no /wiki path) and "
            "JIRA_PROJECT_KEY."
        )
    resp.raise_for_status()


def _adf_description(description: str, file_path: str, start_line: int,
                     end_line: int, suggested_fix: str | None) -> dict:
    """Jira Cloud v3 requires Atlassian Document Format for descriptions."""
    loc = f"{file_path}:{start_line}" + (f"-{end_line}" if end_line != start_line else "")
    code_text = loc + (f"\n\nSuggested fix:\n{suggested_fix}" if suggested_fix else "")
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": description}]},
        {"type": "codeBlock", "attrs": {"language": "text"},
         "content": [{"type": "text", "text": code_text}]},
    ]}


def create_issue(client: httpx.Client, project_key: str, issue_type: str,
                 finding: dict) -> dict:
    """Create one issue from an approved finding. Returns {id, key, url}."""
    payload = {"fields": {
        "project": {"key": project_key},
        # Jira caps summaries at 255 bytes.
        "summary": f"[{finding['severity'].upper()}] {finding['title']}"[:255],
        "issuetype": {"name": issue_type},
        "description": _adf_description(
            finding["description"], finding["file_path"],
            finding["start_line"], finding["end_line"],
            finding.get("suggested_fix"),
        ),
        "labels": [f"coderlens-{finding['category']}"],
    }}
    resp = client.post("/rest/api/3/issue", json=payload)
    _check(resp)
    body = resp.json()
    return {
        "id": body.get("id", ""),
        "key": body.get("key", ""),
        "url": f"{str(client.base_url).rstrip('/')}/browse/{body.get('key', '')}",
    }
