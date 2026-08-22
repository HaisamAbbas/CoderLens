"""Orchestrate Phase 1 ingestion of a repository's streams into Postgres.

Idempotent per repo: each stream's rows are cleared and re-inserted on each run.
`only_issues=True` refreshes just the issues/PR stream against an already-ingested
repo (skips the slow clone + git-history pass).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, insert, select

from archaeologist.config import settings
from archaeologist.ingestion import code_walker, git_history, github_issues, repository
from archaeologist.models.db import init_db, session_scope
from archaeologist.models.entities import Commit, CommitFile, File, Issue, Repo


@dataclass
class IngestStats:
    repo_url: str = ""
    files: int = 0
    commits: int = 0
    commit_files: int = 0
    issues: int = 0
    prs: int = 0
    notes: list[str] = field(default_factory=list)


def ingest_repository(
    repo_url: str | None = None,
    max_commits: int | None = None,
    max_issues: int | None = 500,
    skip_issues: bool = False,
    only_issues: bool = False,
    token: str = "",
) -> IngestStats:
    """`token` (optional GitHub PAT) authenticates the clone of a private repo
    transiently (see repository.clone_or_open) and falls back to the global
    settings.github_token for the issues/PRs fetch when omitted."""
    repo_url = repo_url or settings.target_repo_url
    stats = IngestStats(repo_url=repo_url)
    init_db()

    with session_scope() as session:
        repo_id = (
            _refresh_only_issues(session, repo_url, max_issues, stats)
            if only_issues
            else _full_ingest(session, repo_url, max_commits, stats, token)
        )

        if skip_issues:
            stats.notes.append("issues skipped (--skip-issues)")
            print("[4/5] Issues/PRs: skipped")
        else:
            _ingest_issues(session, repo_id, repo_url, max_issues, stats, token)

    print("[5/5] Done.")
    return stats


def _effective_clone_token(token: str) -> str:
    """Per-repo token (typed in the Add form) wins; otherwise fall back to the
    global GITHUB_TOKEN — so one well-scoped PAT set in .env covers every
    private repo without retyping. Whitespace counts as blank."""
    return (token or "").strip() or settings.github_token


def _full_ingest(session, repo_url: str, max_commits: int | None, stats: IngestStats,
                 token: str = "") -> int:
    print(f"[1/5] Cloning / opening {repo_url} ...")
    repos_dir = Path(settings.repos_dir).resolve()
    git_repo, dest = repository.clone_or_open(repo_url, repos_dir,
                                              _effective_clone_token(token))
    branch, head_sha = repository.head_info(git_repo)
    print(f"      -> {dest}  (branch={branch}, head={head_sha[:8] if head_sha else '?'})")

    repo = session.scalar(select(Repo).where(Repo.url == repo_url))
    if repo is None:
        repo = Repo(url=repo_url)
        session.add(repo)
    repo.name = repository.repo_slug(repo_url)[1]
    repo.default_branch = branch
    repo.head_sha = head_sha
    repo.cloned_path = str(dest)
    repo.ingested_at = datetime.now(timezone.utc)
    session.flush()
    repo_id = repo.id

    for model in (File, Commit, CommitFile):
        session.execute(delete(model).where(model.repo_id == repo_id))

    print("[2/5] Walking working tree (code / docs / config) ...")
    file_rows = code_walker.walk_files(dest)
    _bulk_insert(session, File, file_rows, repo_id)
    stats.files = len(file_rows)
    print(f"      -> {stats.files} files")

    print(f"[3/5] Reading git history (max_commits={max_commits or 'all'}) ...")
    commit_rows, commit_file_rows = git_history.walk_commits(git_repo, max_commits)
    _bulk_insert(session, Commit, commit_rows, repo_id)
    _bulk_insert(session, CommitFile, commit_file_rows, repo_id)
    stats.commits = len(commit_rows)
    stats.commit_files = len(commit_file_rows)
    print(f"      -> {stats.commits} commits, {stats.commit_files} file-changes")
    return repo_id


def _refresh_only_issues(session, repo_url: str, max_issues, stats: IngestStats) -> int:
    repo = session.scalar(select(Repo).where(Repo.url == repo_url))
    if repo is None:
        raise SystemExit(
            f"{repo_url} has not been ingested yet — run a full ingest first "
            "(without --only-issues)."
        )
    print(f"[only-issues] refreshing issues for {repo_url} (repo_id={repo.id})")
    return repo.id


def _ingest_issues(session, repo_id: int, repo_url: str, max_issues, stats: IngestStats,
                   token: str = "") -> None:
    print(f"[4/5] Fetching issues/PRs (max_issues={max_issues or 'all'}) ...")
    # A per-repo token (supplied at add time for a private repo) also
    # authenticates the issues/PRs fetch for that same repo; the global
    # settings.github_token remains the fallback.
    token = token or settings.github_token
    if not token:
        stats.notes.append("no GITHUB_TOKEN: capped to 50 to avoid the 60/hr limit")
        max_issues = min(max_issues or 50, 50)
    # Non-GitHub hosts, a private repo without a token, or a wrong owner/repo
    # all 404 here — that used to raise and, since this runs in the same
    # transaction as the rest of ingestion, roll back the file walk and git
    # history that had already succeeded. Issues/PRs are supplementary
    # evidence, not load-bearing: degrade to "zero issues" and keep the repo
    # that DID ingest, rather than losing everything over one stream.
    try:
        issue_rows = github_issues.fetch_issues(repo_url, token, max_issues)
    except Exception as exc:  # noqa: BLE001 - any failure here is non-fatal
        stats.notes.append(f"issues/PRs unavailable: {exc}")
        print(f"      -> skipped ({exc})")
        return

    session.execute(delete(Issue).where(Issue.repo_id == repo_id))
    _bulk_insert(session, Issue, issue_rows, repo_id)
    stats.issues = sum(1 for r in issue_rows if not r["is_pull_request"])
    stats.prs = sum(1 for r in issue_rows if r["is_pull_request"])
    print(f"      -> {stats.issues} issues, {stats.prs} PRs")


def _bulk_insert(session, model, rows: list[dict], repo_id: int) -> None:
    if not rows:
        return
    for row in rows:
        row["repo_id"] = repo_id
    session.execute(insert(model), rows)
