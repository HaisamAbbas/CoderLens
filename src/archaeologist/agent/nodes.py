"""Investigation nodes. Each returns a partial state update for LangGraph.

Flow: plan -> retrieve -> grade -> (retrieve | synthesize).
The LLM (Gemini by default) drives plan and grade; retrieval and graph
expansion are deterministic tools.
"""

from archaeologist.agent import tools
from archaeologist.agent.state import InvestigationState
from archaeologist.rag import prompts
from archaeologist.rag.llm import call_llm, call_llm_stream, llm_available, parse_llm_json

MAX_EVIDENCE = 24
MAX_HISTORY_TURNS = 4  # enough for pronoun/context resolution without bloating every prompt


def _history_block(history: list[dict] | None) -> str:
    """Prior turns rendered as plain text context — used by both planning and
    synthesis so a follow-up like "what about its callers?" resolves against
    the actual preceding conversation instead of being planned/answered cold."""
    if not history:
        return ""
    recent = history[-MAX_HISTORY_TURNS:]
    lines = [f"Q: {h.get('question', '')}\nA: {h.get('answer', '')}" for h in recent if h.get("question")]
    return "\n\n# Prior conversation (for context — this turn's evidence still governs the answer)\n" + "\n\n".join(lines) if lines else ""

PLAN_SYS = """You plan an investigation of a codebase to answer a question with evidence.
If prior conversation turns are given, use them ONLY to resolve references in the new
question (e.g. "it", "that function", "what about the caller") to concrete names — the
plan must still target the NEW question, not repeat the old one.
Return ONLY JSON:
{"search_queries": [up to 4 focused search strings],
 "graph_targets": [fully-qualified symbol names like "Flask.dispatch_request" to inspect
   dependencies/execution-paths for — use ONLY when the question is about impact
   ("what breaks"), coupling, or call flow; else []],
 "streams": [subset of "code","doc","commit","issue"] or null for all}"""

GRADE_SYS = """You judge whether the collected evidence is enough to answer the question
with citations across code/docs/commits/issues. Return ONLY JSON:
{"sufficient": true|false,
 "missing": "one line on what is still missing (empty if sufficient)",
 "followup_queries": [up to 3 new search strings targeting the gap; [] if sufficient]}"""


def plan_node(state: InvestigationState) -> dict:
    q = state["question"]
    if not llm_available():
        # Offline mode: retrieve the question itself across all streams.
        return {
            "queries": [q],
            "graph_targets": [],
            "streams": None,
            "trace": state["trace"] + [f"PLAN offline (no LLM) — retrieving {q!r}"],
        }
    raw = call_llm(PLAN_SYS, f"Question: {q}" + _history_block(state.get("history")), max_tokens=400)
    plan = parse_llm_json(raw, {"search_queries": [q], "graph_targets": [], "streams": None})
    queries = (plan.get("search_queries") or [q])[:4]
    targets = plan.get("graph_targets") or []
    streams = plan.get("streams")
    return {
        "queries": queries,
        "graph_targets": targets,
        "streams": streams,
        "trace": state["trace"] + [f"PLAN queries={queries} graph_targets={targets} streams={streams}"],
    }


def retrieve_node(state: InvestigationState) -> dict:
    evidence = list(state["evidence"])
    seen = {(e["stream"], e["citation"], e["title"]) for e in evidence}

    for hit in tools.search(state["queries"], state.get("streams")):
        key = (hit["stream"], hit["citation"], hit["title"])
        if key not in seen:
            seen.add(key)
            evidence.append(hit)

    if state["iterations"] == 0 and state.get("graph_targets"):
        for ev in tools.graph_expand(state["graph_targets"]):
            key = (ev["stream"], ev["citation"], ev["title"])
            if key not in seen:
                seen.add(key)
                evidence.append(ev)

    evidence = evidence[:MAX_EVIDENCE]
    return {
        "evidence": evidence,
        "trace": state["trace"] + [f"RETRIEVE iter={state['iterations']} -> {len(evidence)} evidence"],
    }


def grade_node(state: InvestigationState) -> dict:
    summary = "\n".join(
        f"[{i + 1}] ({e['stream']}) {e['citation']} {e['title']}"
        for i, e in enumerate(state["evidence"])
    )
    if not llm_available():
        # Offline mode: enough evidence across streams → answer; otherwise stop
        # and synthesize what we have (no follow-up loop without an LLM).
        sufficient = len(state["evidence"]) >= 3
        missing = ("" if sufficient
                   else "Only a few evidence items found — the answer will be extractive.")
        return {
            "sufficient": sufficient,
            "missing": missing,
            "queries": [],
            "iterations": state["iterations"] + 1,
            "trace": state["trace"] + [
                f"GRADE offline sufficient={sufficient} evidence={len(state['evidence'])}"
            ],
        }
    raw = call_llm(GRADE_SYS, f"Question: {state['question']}\n\nEvidence:\n{summary}", max_tokens=300)
    g = parse_llm_json(raw, {"sufficient": True, "missing": "", "followup_queries": []})
    sufficient = bool(g.get("sufficient"))
    followups = g.get("followup_queries") or []
    return {
        "sufficient": sufficient,
        "missing": g.get("missing", ""),
        "queries": followups,
        "iterations": state["iterations"] + 1,
        "trace": state["trace"] + [f"GRADE sufficient={sufficient} missing={g.get('missing', '')[:70]!r}"],
    }


def synthesize_node(state: InvestigationState) -> dict:
    if not llm_available():
        # Offline mode: an extractive, fully-cited digest instead of a generation.
        answer = prompts.build_digest(state["question"], state["evidence"])
        return {"answer": answer, "trace": state["trace"] + ["SYNTHESIZE (offline digest)"]}
    prompt = prompts.build_prompt(state["question"], state["evidence"], state.get("history"), state.get("simple", False))
    answer = call_llm(prompts.SYSTEM, prompt, max_tokens=1200, label="investigate-synthesize")
    return {"answer": answer, "trace": state["trace"] + ["SYNTHESIZE"]}


def synthesize_stream(state: InvestigationState):
    """Same synthesis as `synthesize_node`, but yields the answer as text deltas
    for token-level streaming. Offline mode has nothing to stream token-by-token
    (the digest is assembled instantly, not generated) — it's yielded as one chunk
    so callers see identical behavior either way, just via the same interface."""
    if not llm_available():
        yield prompts.build_digest(state["question"], state["evidence"])
        return
    prompt = prompts.build_prompt(state["question"], state["evidence"], state.get("history"), state.get("simple", False))
    yield from call_llm_stream(prompts.SYSTEM, prompt, max_tokens=1200, label="investigate-synthesize-stream")


def route_after_grade(state: InvestigationState) -> str:
    if state["sufficient"] or state["iterations"] >= state["max_iterations"] or not state["queries"]:
        return "synthesize"
    return "retrieve"
