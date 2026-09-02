"""Assemble the investigation agent as a LangGraph state machine.

    plan → retrieve → grade → (retrieve again if evidence is thin) → END

Synthesis is deliberately NOT a graph node: it's called directly by `investigate`
(blocking) or `investigate_stream` (token-streaming), because LangGraph's own
value-streaming only yields whole-state snapshots after a node finishes — it
can't stream partial output *from inside* a node. Keeping synthesis outside the
graph is what makes token-by-token streaming to the client possible at all.
"""

import logging
from functools import lru_cache

from langgraph.graph import END, StateGraph

from archaeologist.agent import nodes
from archaeologist.agent.state import InvestigationState

_logger = logging.getLogger("archaeologist")


@lru_cache
def build_agent():
    builder = StateGraph(InvestigationState)
    builder.add_node("plan", nodes.plan_node)
    builder.add_node("retrieve", nodes.retrieve_node)
    builder.add_node("grade", nodes.grade_node)

    builder.set_entry_point("plan")
    builder.add_edge("plan", "retrieve")
    builder.add_edge("retrieve", "grade")
    builder.add_conditional_edges(
        "grade", nodes.route_after_grade,
        {"retrieve": "retrieve", "synthesize": END},  # "synthesize" here just means "done" — see module docstring
    )
    return builder.compile()


def _initial_state(
    question: str, repo_id: int, user_id: int, max_iterations: int,
    history: list[dict] | None, simple: bool = False,
) -> InvestigationState:
    return {
        "question": question,
        "repo_id": repo_id,
        "user_id": user_id,
        "history": history or [],
        "queries": [],
        "graph_targets": [],
        "streams": None,
        "evidence": [],
        "iterations": 0,
        # Clamped here too, not just at the API layer (routers/api.py's
        # Field(le=5)) — this is the one place every caller (HTTP, CLI,
        # notebook) funnels through, so the graph itself can never run an
        # unbounded number of LLM calls no matter how it's invoked.
        "max_iterations": max(1, min(max_iterations, 5)),
        "sufficient": False,
        "missing": "",
        "answer": "",
        "trace": [],
        "simple": simple,
    }


def investigate(
    question: str, repo_id: int, user_id: int, max_iterations: int = 2,
    history: list[dict] | None = None, simple: bool = False,
) -> dict:
    agent = build_agent()
    state = agent.invoke(_initial_state(question, repo_id, user_id, max_iterations, history, simple))
    state.update(nodes.synthesize_node(state))
    return state


def investigate_stream(
    question: str, repo_id: int, user_id: int, max_iterations: int = 2,
    history: list[dict] | None = None, simple: bool = False,
):
    """Generator yielding SSE-style events as the investigation runs:

    {"type": "step", "message": "PLAN queries=..."}     — one per node
    {"type": "answer_delta", "text": "..."}             — one per synthesized token chunk
    {"type": "answer", "answer": "..."}                 — final, complete synthesis
    {"type": "evidence", "evidence": [...]}             — final evidence list
    {"type": "error", "message": "..."}                 — on failure

    Uses LangGraph's value-streaming (full state after each node) and diffs the
    trace so the UI sees steps the moment they happen instead of at the end;
    synthesis then streams its own token deltas on top (see module docstring).
    """
    agent = build_agent()
    initial = _initial_state(question, repo_id, user_id, max_iterations, history, simple)
    try:
        seen = 0
        final = None
        for state in agent.stream(initial, stream_mode="values"):
            final = state
            trace = state.get("trace") or []
            for entry in trace[seen:]:
                yield {"type": "step", "message": entry}
            seen = len(trace)
        if final is not None:
            answer = ""
            for delta in nodes.synthesize_stream(final):
                answer += delta
                yield {"type": "answer_delta", "text": delta}
            yield {"type": "answer", "answer": answer}
            yield {"type": "evidence", "evidence": final.get("evidence", [])}
    except Exception:  # noqa: BLE001 - logged; the client gets a generic message (see M-10)
        _logger.exception("investigate_stream failed for repo %s", repo_id)
        yield {"type": "error", "message": "The investigation failed. Check provider configuration."}
