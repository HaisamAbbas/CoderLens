"""Unit tests for private-repo clone support — the URL credential embedding,
the .git/config scrub on success, and the error-message scrub on failure.
All git interactions are faked at the GitPython boundary; live verification
against a real private repo is a manual step per the phase plan.
"""

from types import SimpleNamespace

import pytest
from git import Repo as GitRepo

from archaeologist.ingestion.repository import _with_token, clone_or_open

TOKEN = "ghp_secret123"
URL = "https://github.com/owner/repo"


def test_with_token_embeds_basic_creds_for_https():
    assert _with_token(URL, TOKEN) == f"https://x-access-token:{TOKEN}@github.com/owner/repo"


def test_with_token_preserves_path_and_query():
    url = "https://github.com/owner/repo.git"
    assert _with_token(url, TOKEN).endswith("@github.com/owner/repo.git")
    # A URL copied from a browser (with browse path) keeps its extra segments.
    assert "/tree/main" in _with_token("https://github.com/o/r/tree/main", TOKEN)


def test_with_token_leaves_non_https_untouched():
    assert _with_token("http://github.com/owner/repo", TOKEN) == "http://github.com/owner/repo"
    # API validation only admits http(s), but stay defensive for ssh forms.
    assert _with_token("git@github.com:owner/repo.git", TOKEN) == "git@github.com:owner/repo.git"


def test_clone_scrubs_origin_url_after_authenticated_clone(tmp_path, monkeypatch):
    recorded = {}
    scrubbed = []

    def fake_clone(url, dest, **kw):
        recorded["clone_url"] = url
        remote = SimpleNamespace(set_url=lambda new_url: scrubbed.append(new_url))
        return SimpleNamespace(remotes=SimpleNamespace(origin=remote))

    monkeypatch.setattr(GitRepo, "clone_from", staticmethod(fake_clone))
    got_repo, dest = clone_or_open(URL, tmp_path, token=TOKEN)

    assert recorded["clone_url"] == f"https://x-access-token:{TOKEN}@github.com/owner/repo"
    assert got_repo is not None
    assert scrubbed == [URL]  # bare, token-free URL restored into .git/config


def test_clone_without_token_never_touches_origin(tmp_path, monkeypatch):
    recorded = {}

    def fake_clone(url, dest, **kw):
        recorded["clone_url"] = url
        return SimpleNamespace(remotes=SimpleNamespace(origin=None))

    monkeypatch.setattr(GitRepo, "clone_from", staticmethod(fake_clone))
    clone_or_open(URL, tmp_path)
    assert recorded["clone_url"] == URL


def test_failed_clone_message_masks_token_and_has_hint(tmp_path, monkeypatch):
    def fake_clone(url, dest, **kw):
        raise RuntimeError(
            f"Cmd('git') failed: fatal: unable to access '{url}': auth failed")

    monkeypatch.setattr(GitRepo, "clone_from", staticmethod(fake_clone))
    with pytest.raises(RuntimeError) as err:
        clone_or_open(URL, tmp_path, token=TOKEN)
    msg = str(err.value)
    assert TOKEN not in msg          # masked before it can reach IngestJob.error/UI
    assert "***" in msg
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
