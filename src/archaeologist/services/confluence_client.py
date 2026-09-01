"""Thin sync REST wrapper over the Confluence Cloud v1 content API.

Mirrors `ingestion/github_issues.py`: a small httpx client with plain
functions, `raise_for_status()` plus a clear RuntimeError on auth failures.
Auth is Basic (email + API token) — create a token at
id.atlassian.com/manage-profile/security/api-tokens.

`base_url` includes the /wiki context path (e.g.
https://your-domain.atlassian.net/wiki), so every request path here is
relative to it, exactly as Atlassian's docs show for Cloud.

Credentials are passed in explicitly (Phase 4 of the multi-user migration —
each user brings their own, decrypted from services/user_integrations.py),
never read from global settings.
"""

from dataclasses import dataclass

import httpx


@dataclass
class ConfluencePage:
    id: str
    title: str
    version: int
    url: str


def open_client(base_url: str, email: str, api_token: str) -> httpx.Client:
    return httpx.Client(auth=(email, api_token), base_url=base_url, timeout=30.0)


def _page_url(client: httpx.Client, body: dict) -> str:
    webui = (body.get("_links") or {}).get("webui", "")
    return f"{str(client.base_url).rstrip('/')}{webui}" if webui else ""


def _check(resp: httpx.Response) -> None:
    if resp.status_code in (401, 403):
        raise RuntimeError(
            f"Confluence rejected the credentials ({resp.status_code}). "
            "Check your email/API token in Settings and that the token has not expired."
        )
    if resp.status_code == 404:
        raise RuntimeError(
            "Confluence returned 404 — check your base URL (must include the "
            "/wiki context path) and space key in Settings."
        )
    resp.raise_for_status()


def find_page_by_title(
    client: httpx.Client, space_key: str, title: str
) -> ConfluencePage | None:
    """Title is unique per space, so this is a safe create-vs-update lookup."""
    resp = client.get(
        "/rest/api/content",
        params={"spaceKey": space_key, "title": title, "expand": "version"},
    )
    _check(resp)
    results = resp.json().get("results", [])
    if not results:
        return None
    page = results[0]
    return ConfluencePage(
        id=page["id"],
        title=page["title"],
        version=(page.get("version") or {}).get("number", 1),
        url=_page_url(client, page),
    )


def create_page(
    client: httpx.Client,
    space_key: str,
    title: str,
    body_html: str,
    parent_id: str | None = None,
) -> ConfluencePage:
    payload: dict = {
        "type": "page",
        "title": title,
        "space": {"key": space_key},
        "body": {"storage": {"value": body_html, "representation": "storage"}},
    }
    if parent_id:
        payload["ancestors"] = [{"id": parent_id}]
    resp = client.post("/rest/api/content", json=payload)
    _check(resp)
    page = resp.json()
    return ConfluencePage(
        id=page["id"], title=page["title"], version=1, url=_page_url(client, page)
    )


def update_page(
    client: httpx.Client, page: ConfluencePage, body_html: str
) -> ConfluencePage:
    """Optimistic concurrency: version.number must be current + 1."""
    payload = {
        "id": page.id,
        "type": "page",
        "title": page.title,
        "version": {"number": page.version + 1},
        "body": {"storage": {"value": body_html, "representation": "storage"}},
    }
    resp = client.put(f"/rest/api/content/{page.id}", json=payload)
    _check(resp)
    updated = resp.json()
    return ConfluencePage(
        id=updated["id"],
        title=updated["title"],
        version=(updated.get("version") or {}).get("number", page.version + 1),
        url=_page_url(client, updated),
    )


def upload_attachment(
    client: httpx.Client,
    page_id: str,
    filename: str,
    content: bytes,
    media_type: str = "image/png",
) -> None:
    """Re-uploading an existing filename auto-versions that attachment (no
    orphans from repeat publishes). Requires the X-Atlassian-Token header to
    skip Confluence's XSRF check for multipart uploads."""
    resp = client.post(
        f"/rest/api/content/{page_id}/child/attachment",
        files={"file": (filename, content, media_type)},
        headers={"X-Atlassian-Token": "nocheck"},
    )
    _check(resp)
