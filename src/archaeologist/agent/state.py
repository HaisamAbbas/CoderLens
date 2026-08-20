"""Shared state for the investigation agent (LangGraph)."""

from typing import TypedDict


class InvestigationState(TypedDict):
    question: str
    history: list[dict]         # prior turns this conversation: [{"question","answer"}, ...]
    queries: list[str]          # current search queries (from plan, then grade follow-ups)
    graph_targets: list[str]    # qualified symbol names to expand via the dependency graph
    streams: list[str] | None   # stream filter (code/doc/commit/issue) or None for all
    evidence: list[dict]        # accumulated, deduped evidence
    iterations: int
    max_iterations: int
    sufficient: bool
    missing: str
    answer: str
    trace: list[str]            # human-readable investigation log
    simple: bool                # if true, synthesis favors plain, jargon-explained wording
