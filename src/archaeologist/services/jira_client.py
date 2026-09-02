"""Thin sync REST wrapper over the Jira Cloud v3 issue API.

Mirrors `services/confluence_client.py`: small httpx client with plain
functions and a clear RuntimeError on auth failures. Same Basic(email, API
token) scheme as Confluence — but credentials are independent per feature,
so each must be configured on its own.

Credentials are passed in explicitly (Phase 4 of the multi-user migration —
each user brings their own), never read from global settings.

Two Jira-specific wrinkles:
- base_url has NO /wiki path segment (unlike Confluence's).
- Descriptions must be Atlassian Document Format (ADF), not HTML/markdown.
"""

import re

import httpx

from archaeologist.net_guard import assert_public_https_url

# Findings are LLM-generated from a scanned file's own (attacker-influenced)
# content — see analysis/weaknesses.py's untrusted-content boundary. That
# boundary is the main defense, but a successful injection could still make
# the model emit a URL in a finding's title/description, and Jira auto-links
# bare URLs in rendered text — turning a forged ticket into a clickable
# phishing link for whoever reviews it. A weakness finding never legitimately
# needs to link out, so strip anything URL-shaped before it reaches Jira.
_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)


def _strip_urls(text: str) -> str:
    return _URL_RE.sub("[link removed]", text)


def open_client(base_url: str, email: str, api_token: str) -> httpx.Client:
    # base_url is validated at save time (services/user_integrations.py) too,
    # but DNS can be re-pointed after saving — re-check right before the
    # credentialed client is actually used.
    assert_public_https_url(base_url)
    return httpx.Client(auth=(email, api_token), base_url=base_url, timeout=30.0,
                        follow_redirects=False)


def _check(resp: httpx.Response) -> None:
    if resp.status_code in (401, 403):
        raise RuntimeError(
            f"Jira rejected the credentials ({resp.status_code}). "
            "Check your email/API token in Settings and that the token has not expired."
        )
    if resp.status_code == 404:
        raise RuntimeError(
            "Jira returned 404 — check your base URL (no /wiki path) and "
            "project key in Settings."
        )
    resp.raise_for_status()


def _adf_description(description: str, file_path: str, start_line: int,
                     end_line: int, suggested_fix: str | None) -> dict:
    """Jira Cloud v3 requires Atlassian Document Format for descriptions."""
    loc = f"{file_path}:{start_line}" + (f"-{end_line}" if end_line != start_line else "")
    fix_text = _strip_urls(suggested_fix) if suggested_fix else None
    code_text = loc + (f"\n\nSuggested fix:\n{fix_text}" if fix_text else "")
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": _strip_urls(description)}]},
        {"type": "codeBlock", "attrs": {"language": "text"},
         "content": [{"type": "text", "text": code_text}]},
    ]}


def create_issue(client: httpx.Client, project_key: str, issue_type: str,
                 finding: dict) -> dict:
    """Create one issue from an approved finding. Returns {id, key, url}."""
    payload = {"fields": {
        "project": {"key": project_key},
        # Jira caps summaries at 255 bytes.
        "summary": _strip_urls(f"[{finding['severity'].upper()}] {finding['title']}")[:255],
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
