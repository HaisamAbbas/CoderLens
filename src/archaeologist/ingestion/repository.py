"""Clone (or open) the target repository on disk."""

from pathlib import Path
from urllib.parse import urlparse

import git


def repo_slug(url: str) -> tuple[str, str]:
    """('https://github.com/pallets/flask') -> ('pallets', 'flask')."""
    path = urlparse(url).path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    owner, _, name = path.partition("/")
    return owner, name


def clone_or_open(url: str, repos_dir: Path) -> tuple[git.Repo, Path]:
    """Full clone (needed for git history) into repos_dir/<owner>__<name>.
    If it already exists, open it in place."""
    owner, name = repo_slug(url)
    dest = repos_dir / f"{owner}__{name}"
    if (dest / ".git").exists():
        return git.Repo(dest), dest
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
