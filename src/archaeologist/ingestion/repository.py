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


def clone_or_open(url: str, repos_dir: Path) -> tuple[git.Repo, Path]:
    """Full clone (needed for git history) into repos_dir/<owner>__<name>.
    If it already exists, open it in place."""
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
    repo = git.Repo.clone_from(url, dest)
    return repo, dest


def head_info(repo: "git.Repo") -> tuple[str | None, str | None]:
    """Return (default_branch, head_sha)."""
    try:
        branch = repo.active_branch.name
    except TypeError:  # detached HEAD
        branch = None
    return branch, repo.head.commit.hexsha
