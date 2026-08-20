"""Unit tests for the API-key-free / offline mode: provider resolution and the
deterministic fallbacks the app uses when no LLM (hosted or local) is available.

These tests need no database, OpenSearch, or network — the agent nodes' offline
paths never call the LLM or retrieval.
"""

import pytest

from archaeologist.agent import nodes
from archaeologist.agent.state import InvestigationState
from archaeologist.config import settings
from archaeologist.rag import llm, prompts


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    """Force auto-resolution with no hosted keys and no reachable Ollama."""
    monkeypatch.setattr(settings, "llm_provider", "auto")
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(llm, "ollama_available", lambda: False)


def _state(**kw) -> InvestigationState:
    base: InvestigationState = {
        "question": "Why does Flask use an application context?",
        "queries": [], "graph_targets": [], "streams": None, "evidence": [],
        "iterations": 0, "max_iterations": 2, "sufficient": False,
        "missing": "", "answer": "", "trace": [],
    }
    base.update(kw)
    return base


def test_resolve_none_without_any_llm():
    assert llm.resolve_provider() is None
    assert llm.llm_available() is False
    assert llm.has_api_key() is False  # backwards-compatible alias


def test_local_ollama_is_used_when_up(monkeypatch):
    monkeypatch.setattr(llm, "ollama_available", lambda: True)
    assert llm.resolve_provider() == "ollama"
    assert llm.llm_available() is True


def test_hosted_key_wins_over_ollama(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(llm, "ollama_available", lambda: True)
    assert llm.resolve_provider() == "gemini"


def test_explicit_gemini_without_key_is_unavailable():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    try:
        assert llm.resolve_provider() is None
    finally:
        monkeypatch.undo()


def test_build_digest_is_cited_and_clearly_offline():
    evidence = [
        {"stream": "code", "citation": "src/flask/app.py:110", "title": "Flask",
         "body": "The flask object implements a WSGI application."},
        {"stream": "commit", "citation": "commit abc1234", "title": "fix context handling",
         "snippet": "pushes the app context"},
    ]
    out = prompts.build_digest("Why an app context?", evidence)
    assert "Why an app context?" in out
    assert "src/flask/app.py:110" in out
    assert "commit abc1234" in out
    assert "Offline mode" in out


def test_build_digest_empty_evidence():
    out = prompts.build_digest("q", [])
    assert "No evidence found" in out


def test_plan_node_offline_is_deterministic():
    st = nodes.plan_node(_state())
    assert st["queries"] == [_state()["question"]]
    assert st["graph_targets"] == []
    assert st["trace"][0].startswith("PLAN offline")


def test_grade_node_offline_sufficient_with_3_evidence():
    ev = [{"stream": "code", "citation": f"f{i}.py:1", "title": "t"} for i in range(3)]
    st = nodes.grade_node(_state(evidence=ev))
    assert st["sufficient"] is True
    assert st["queries"] == []


def test_grade_node_offline_insufficient_with_little_evidence():
    ev = [{"stream": "code", "citation": "f1.py:1", "title": "t"}]
    st = nodes.grade_node(_state(evidence=ev))
    assert st["sufficient"] is False
    assert st["queries"] == []  # no follow-up loop without an LLM


def test_synthesize_node_offline_returns_digest():
    ev = [{"stream": "code", "citation": "f1.py:1", "title": "t", "body": "x"}]
    st = nodes.synthesize_node(_state(evidence=ev))
    assert "Offline mode" in st["answer"]
    assert "f1.py:1" in st["answer"]
