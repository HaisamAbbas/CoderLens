"""Clone (or open) the target repository on disk."""

import shutil
from pathlib import Path
from urllib.parse import urlparse

import git

# GitHub (and lookalike hosts) segments that mean "this is a browser URL for
# viewing something inside the repo", not the repo itself — e.g. pasting
# straight from the address bar while browsing a branch gives
# ".../owner/repo/tree/main", which `git clone` has no idea what to do with.
_BROWSE_SEGMENTS = ("tree", "blob", "commits", "commit", "pull", "pulls", "issues", "releases", "wiki")


def normalize_repo_url(url: str) -> str:
    """Strip a trailing browser path (/tree/main, /blob/main/README.md, ...)
    down to just the repo itself, e.g.:
    'https://github.com/o/r/tree/main' -> 'https://github.com/o/r'
    Leaves a bare repo URL (with or without .git) untouched."""
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if len(parts) > 2 and parts[2] in _BROWSE_SEGMENTS:
        parts = parts[:2]
        parsed = parsed._replace(path="/" + "/".join(parts))
        return parsed.geturl()
    return url


def repo_slug(url: str) -> tuple[str, str]:
    """('https://github.com/pallets/flask') -> ('pallets', 'flask')."""
    path = urlparse(normalize_repo_url(url)).path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    owner, _, name = path.partition("/")
    return owner, name


def _with_token(url: str, token: str) -> str:
    """Embed a PAT as HTTP Basic creds for the clone call only — GitHub's
    documented PAT-over-HTTPS mechanism. Only https:// URLs support this."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return url
    return parsed._replace(netloc=f"x-access-token:{token}@{parsed.netloc}").geturl()


def clone_or_open(url: str, repos_dir: Path, token: str = "") -> tuple[git.Repo, Path]:
    """Full clone (needed for git history) into repos_dir/<owner>__<name>.
    If it already exists, open it in place.

    `token` (a GitHub PAT) is used transiently at the single clone_from call
    and NEVER persisted: the origin remote is scrubbed back to the bare URL
    immediately after a successful clone (git records the credentialed URL in
    .git/config otherwise), and clone failures are re-raised with the token
    substring masked before the message can reach IngestJob.error or the UI.
    """
    url = normalize_repo_url(url)
    owner, name = repo_slug(url)
    dest = repos_dir / f"{owner}__{name}"
    if (dest / ".git").exists():
        return git.Repo(dest), dest
    # A previous attempt (e.g. a since-corrected malformed URL) can leave an
    # empty or partial directory behind — `git clone` refuses to reuse a
    # non-empty destination even if it's just leftover cruft, so clear it.
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    token = (token or "").strip()
    clone_url = _with_token(url, token) if token else url
    try:
        repo = git.Repo.clone_from(clone_url, dest)
    except Exception as exc:
        msg = str(exc).replace(token, "***") if token else str(exc)
        hint = "" if token else (
            " — if this is a private repository, add a GitHub token when adding it.")
        raise RuntimeError(f"git clone failed: {msg}{hint}") from exc
    if token:
        repo.remotes.origin.set_url(url)   # scrub the token out of .git/config right away
    return repo, dest


def head_info(repo: "git.Repo") -> tuple[str | None, str | None]:
    """Return (default_branch, head_sha)."""
    try:
        branch = repo.active_branch.name
    except TypeError:  # detached HEAD
        branch = None
    return branch, repo.head.commit.hexsha
