"""Fetch the issues/PRs stream from the GitHub REST API.

The /issues endpoint returns both issues and pull requests; a PR is flagged by
the presence of a `pull_request` key. A token lifts the 60 req/hr limit to 5000.
"""

from datetime import datetime

import httpx

from archaeologist.ingestion.repository import _validate_slug, repo_slug

API = "https://api.github.com"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fetch_issues(
    repo_url: str, token: str = "", max_issues: int | None = 500
) -> list[dict]:
    """Return issue/PR rows (without repo_id), newest first."""
    owner, name = repo_slug(repo_url)
    _validate_slug(owner, name)  # a malformed slug must not reshape the API path below
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    rows: list[dict] = []
    page = 1
    with httpx.Client(headers=headers, timeout=30.0) as client:
        while max_issues is None or len(rows) < max_issues:
            resp = client.get(
                f"{API}/repos/{owner}/{name}/issues",
                params={"state": "all", "per_page": 100, "page": page,
                        "sort": "created", "direction": "desc"},
            )
            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                remaining = resp.headers.get("X-RateLimit-Remaining")
                raise RuntimeError(
                    f"GitHub rate limit hit (remaining={remaining}). "
                    "Set GITHUB_TOKEN in .env to raise the limit."
                )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for item in batch:
                rows.append(
                    {
                        "number": item["number"],
                        "is_pull_request": "pull_request" in item,
                        "title": item.get("title"),
                        "body": item.get("body"),
                        "state": item.get("state"),
                        "author": (item.get("user") or {}).get("login"),
                        "labels": [lbl["name"] for lbl in item.get("labels", [])],
                        "comments_count": item.get("comments", 0),
                        "created_at": _parse_dt(item.get("created_at")),
                        "updated_at": _parse_dt(item.get("updated_at")),
                        "closed_at": _parse_dt(item.get("closed_at")),
                        "url": item.get("html_url"),
                    }
                )
            page += 1

    if max_issues is not None:
        rows = rows[:max_issues]
    return rows
