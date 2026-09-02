"""Clone (or open) the target repository on disk."""

import base64
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

import git

from archaeologist.net_guard import assert_public_host

# GitHub (and lookalike hosts) segments that mean "this is a browser URL for
# viewing something inside the repo", not the repo itself — e.g. pasting
# straight from the address bar while browsing a branch gives
# ".../owner/repo/tree/main", which `git clone` has no idea what to do with.
_BROWSE_SEGMENTS = ("tree", "blob", "commits", "commit", "pull", "pulls", "issues", "releases", "wiki")

# A clone destination is built as f"{owner}__{name}" under repos_dir — each
# segment must be a plain filesystem-safe name, never "..", a hidden path
# escape, or anything containing a path separator. Rejecting anything outside
# this set closes both a path-traversal write/delete and (since this same
# owner/name pair is reused to build the GitHub issues API path) argument
# injection into that request.
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")

# Hosts a caller-supplied GitHub PAT is ever attached to. Without this, any
# https:// URL — including one the caller fully controls — would receive the
# credential in its Basic-auth userinfo, handing the token to whoever
# operates that host.
_TOKEN_HOSTS = {"github.com", "www.github.com"}


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


def safe_repo_name(url: str) -> str:
    """The display name stored on Repo.name — derived from the URL (so
    attacker-controlled, same as the clone slug) but stripped down to a
    plain identifier before it's persisted. This is the single point that
    keeps every downstream consumer (the exported-snapshot HTML title, its
    Content-Disposition filename, Mermaid diagram labels) safe without each
    of them having to re-sanitize a value that looks like it's already a
    clean repo name."""
    _, name = repo_slug(url)
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "", name)[:100]
    return cleaned or "repo"


def _validate_slug(owner: str, name: str) -> None:
    """Reject anything that isn't a plain, single-segment filesystem name —
    in particular `..` and embedded `/`, which would otherwise let a crafted
    URL point the clone destination (and the on-failure rmtree) anywhere on
    disk. See the module-level `_SEGMENT_RE` docstring."""
    valid = _SEGMENT_RE.match(owner) and _SEGMENT_RE.match(name)
    if not valid or owner in (".", "..") or name in (".", ".."):
        raise ValueError(f"unsupported repository path: {owner!r}/{name!r}")


def _assert_public_host(url: str) -> None:
    """Refuse to clone from a host that resolves to a private, loopback, or
    link-local address — closes off cloning from cloud metadata endpoints or
    the app's own internal services (Postgres/OpenSearch/Redis) as a
    server-side-request-forgery vector. git's own connection is still the
    authoritative check; see net_guard.assert_public_host's docstring for
    what this does and doesn't defend against."""
    assert_public_host(urlparse(url).hostname)


def _with_token_header(url: str, token: str) -> tuple[str, list[str]]:
    """Return (url, extra_git_args) that authenticate the clone as a GitHub
    PAT, without ever writing the token into the URL itself — an embedded
    URL is both persisted to .git/config until explicitly scrubbed and
    visible in this process's argv to any other local process for the
    clone's duration. Only ever applied for github.com (see _TOKEN_HOSTS);
    for any other host the token is dropped rather than sent, since it isn't
    that host's credential to receive."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in _TOKEN_HOSTS:
        return url, []
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return url, ["-c", f"http.extraHeader=Authorization: Basic {basic}"]


def clone_or_open(url: str, repos_dir: Path, token: str = "") -> tuple[git.Repo, Path]:
    """Full clone (needed for git history) into repos_dir/<owner>__<name>.
    If it already exists, open it in place.

    `token` (a GitHub PAT) is used transiently at the single clone_from call
    and NEVER persisted: it rides as a request header (see
    _with_token_header) rather than in the URL, so nothing credentialed ever
    reaches .git/config, and a failed clone always removes the destination
    outright rather than risking a partial/credentialed leftover.
    """
    url = normalize_repo_url(url)
    owner, name = repo_slug(url)
    _validate_slug(owner, name)
    _assert_public_host(url)
    repos_dir = repos_dir.resolve()
    dest = (repos_dir / f"{owner}__{name}").resolve()
    if dest.parent != repos_dir:
        raise ValueError("clone destination escapes repos_dir")
    if (dest / ".git").exists():
        return git.Repo(dest), dest
    # A previous attempt (e.g. a since-corrected malformed URL) can leave an
    # empty or partial directory behind — `git clone` refuses to reuse a
    # non-empty destination even if it's just leftover cruft, so clear it.
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    token = (token or "").strip()
    clone_url, extra_args = _with_token_header(url, token) if token else (url, [])
    try:
        repo = git.Repo.clone_from(clone_url, dest, multi_options=extra_args,
                                    env={"GIT_TERMINAL_PROMPT": "0"})
    except Exception as exc:
        shutil.rmtree(dest, ignore_errors=True)  # never leave a partial/credentialed clone behind
        msg = str(exc).replace(token, "***") if token else str(exc)
        hint = "" if token else (
            " — if this is a private repository, add a GitHub token when adding it.")
        raise RuntimeError(f"git clone failed: {msg}{hint}") from exc
    return repo, dest


def head_info(repo: "git.Repo") -> tuple[str | None, str | None]:
    """Return (default_branch, head_sha)."""
    try:
        branch = repo.active_branch.name
    except TypeError:  # detached HEAD
        branch = None
    return branch, repo.head.commit.hexsha
