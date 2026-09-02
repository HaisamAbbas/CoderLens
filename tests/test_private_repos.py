"""Unit tests for private-repo clone support — the Basic-auth header used
instead of a URL-embedded credential, the on-failure cleanup, and the
error-message scrub on failure. All git interactions are faked at the
GitPython boundary; the public-host check is stubbed out (it makes a real
DNS lookup, out of scope for these unit tests). Live verification against a
real private repo is a manual step per the phase plan.
"""

import base64
from types import SimpleNamespace

import pytest
from git import Repo as GitRepo

from archaeologist.ingestion.repository import _with_token_header, clone_or_open

TOKEN = "ghp_secret123"
URL = "https://github.com/owner/repo"


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    """clone_or_open resolves the host to reject private/internal targets —
    stub that out so these unit tests never depend on real network access."""
    import archaeologist.ingestion.repository as repository
    monkeypatch.setattr(repository, "_assert_public_host", lambda url: None)


def _basic_header(token: str) -> str:
    return f"Authorization: Basic {base64.b64encode(f'x-access-token:{token}'.encode()).decode()}"


def test_with_token_header_for_github_https():
    url, args = _with_token_header(URL, TOKEN)
    assert url == URL  # the URL itself is never touched
    assert args == ["-c", f"http.extraHeader={_basic_header(TOKEN)}"]


def test_with_token_header_ignored_for_non_github_host():
    url, args = _with_token_header("https://evil.example.com/owner/repo", TOKEN)
    assert url == "https://evil.example.com/owner/repo"
    assert args == []  # never hand this host's server our GitHub PAT


def test_with_token_header_leaves_non_https_untouched():
    url, args = _with_token_header("http://github.com/owner/repo", TOKEN)
    assert args == []
    url, args = _with_token_header("git@github.com:owner/repo.git", TOKEN)
    assert args == []


def test_clone_sends_token_as_header_never_in_url(tmp_path, monkeypatch):
    recorded = {}

    def fake_clone(url, dest, **kw):
        recorded["clone_url"] = url
        recorded["multi_options"] = kw.get("multi_options")
        return SimpleNamespace()

    monkeypatch.setattr(GitRepo, "clone_from", staticmethod(fake_clone))
    got_repo, dest = clone_or_open(URL, tmp_path, token=TOKEN)

    assert recorded["clone_url"] == URL  # never embedded — see _with_token_header
    assert recorded["multi_options"] == ["-c", f"http.extraHeader={_basic_header(TOKEN)}"]
    assert got_repo is not None


def test_clone_without_token_never_touches_origin(tmp_path, monkeypatch):
    recorded = {}

    def fake_clone(url, dest, **kw):
        recorded["clone_url"] = url
        recorded["multi_options"] = kw.get("multi_options")
        return SimpleNamespace()

    monkeypatch.setattr(GitRepo, "clone_from", staticmethod(fake_clone))
    clone_or_open(URL, tmp_path)
    assert recorded["clone_url"] == URL
    assert recorded["multi_options"] == []


def test_failed_clone_removes_partial_destination(tmp_path, monkeypatch):
    def fake_clone(url, dest, **kw):
        # Simulate git having written a partial .git dir before failing.
        (dest / ".git").mkdir(parents=True)
        raise RuntimeError(f"Cmd('git') failed: fatal: unable to access '{url}': auth failed")

    monkeypatch.setattr(GitRepo, "clone_from", staticmethod(fake_clone))
    with pytest.raises(RuntimeError):
        clone_or_open(URL, tmp_path, token=TOKEN)
    assert not (tmp_path / "owner__repo").exists()


def test_failed_clone_message_masks_token_and_has_hint(tmp_path, monkeypatch):
    def fake_clone(url, dest, **kw):
        raise RuntimeError(
            f"Cmd('git') failed: fatal: unable to access '{url}': auth failed")

    monkeypatch.setattr(GitRepo, "clone_from", staticmethod(fake_clone))
    with pytest.raises(RuntimeError) as err:
        clone_or_open(URL, tmp_path, token=TOKEN)
    msg = str(err.value)
    assert TOKEN not in msg          # masked before it can reach IngestJob.error/UI
    assert "git clone failed:" in msg
    assert "add a GitHub token" not in msg  # hint only when NO token was given


def test_clone_falls_back_to_global_token_when_blank(monkeypatch):
    """The user's stated model: one GITHUB_TOKEN in .env covers all their
    private repos — the per-repo field only overrides it."""
    from archaeologist.config import settings
    from archaeologist.ingestion.pipeline import _effective_clone_token

    monkeypatch.setattr(settings, "github_token", "ghp_global_token")
    assert _effective_clone_token("") == "ghp_global_token"
    assert _effective_clone_token("   ") == "ghp_global_token"


def test_per_repo_token_wins_over_global(monkeypatch):
    from archaeologist.config import settings
    from archaeologist.ingestion.pipeline import _effective_clone_token

    monkeypatch.setattr(settings, "github_token", "ghp_global_token")
    assert _effective_clone_token("ghp_local") == "ghp_local"


def test_failed_public_clone_hints_at_private_repo(tmp_path, monkeypatch):
    def fake_clone(url, dest, **kw):
        raise RuntimeError("fatal: repository 'https://github.com/owner/repo/' not found")

    monkeypatch.setattr(GitRepo, "clone_from", staticmethod(fake_clone))
    with pytest.raises(RuntimeError) as err:
        clone_or_open(URL, tmp_path)
    assert "add a GitHub token" in str(err.value)


def test_failed_clone_with_empty_token_masks_nothing_but_still_safe(tmp_path, monkeypatch):
    """A blank token must never turn messages into '***' soup."""
    def fake_clone(url, dest, **kw):
        raise RuntimeError("could not read Username")

    monkeypatch.setattr(GitRepo, "clone_from", staticmethod(fake_clone))
    with pytest.raises(RuntimeError) as err:
        clone_or_open(URL, tmp_path, token="   ")
    assert str(err.value) == (
        "git clone failed: could not read Username — "
        "if this is a private repository, add a GitHub token when adding it.")


def test_rejects_path_traversal_in_slug(tmp_path):
    with pytest.raises(ValueError):
        clone_or_open("https://github.com/owner/../../../etc", tmp_path)


def test_rejects_traversal_via_repo_name_segment(tmp_path):
    # normalize_repo_url only strips *known browse segments* — a name with an
    # embedded ".." must still be rejected by the slug validator itself.
    with pytest.raises(ValueError):
        clone_or_open("https://github.com/owner/..%2f..%2fetc", tmp_path)
