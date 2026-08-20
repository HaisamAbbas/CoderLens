"""Impact analysis — "is it safe to change this?" for a single symbol.

Three signals already exist separately elsewhere in the app (direct/
transitive callers via the call graph, change coupling, dead-code/entrypoint
context) plus one new one (test coverage, inferred from whether any test-file
symbol calls this within the same upstream walk). This stitches all four into
one grounded verdict instead of making someone piece it together across three
different pages before touching unfamiliar code.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from archaeologist.analysis.coupling import find_change_coupling
from archaeologist.analysis.entrypoints import find_entrypoints
from archaeologist.models.entities import Symbol, SymbolEdge

DEPTH = 3
MAX_NODES = 60


def _upstream(session: Session, symbol_id: int) -> dict[int, dict]:
    """BFS over reverse call edges — everything (transitively) that calls this,
    layered by hop distance. Mirrors `call_flow`'s downstream walk in
    retrieval/graph_queries.py, just in the opposite direction."""
    nodes: dict[int, dict] = {}
    seen_edge: set[tuple[int, int]] = set()
    frontier = [symbol_id]
    for d in range(1, DEPTH + 1):
        if not frontier or len(nodes) >= MAX_NODES:
            break
        rows = session.execute(
            select(SymbolEdge.dst_symbol_id, Symbol)
            .join(Symbol, Symbol.id == SymbolEdge.src_symbol_id)
            .where(SymbolEdge.dst_symbol_id.in_(frontier),
                   SymbolEdge.edge_type == "call",
                   SymbolEdge.src_symbol_id.is_not(None))
        ).all()
        next_frontier: list[int] = []
        for dst, sym in rows:
            key = (sym.id, dst)
            if key in seen_edge or sym.id == dst:
                continue
            seen_edge.add(key)
            if sym.id not in nodes and len(nodes) < MAX_NODES:
                nodes[sym.id] = {
                    "id": sym.id, "qualified_name": sym.qualified_name, "kind": sym.kind,
                    "file": sym.file_path, "line": sym.start_line, "depth": d,
                }
                next_frontier.append(sym.id)
        frontier = next_frontier
    return nodes


def _risk_verdict(fan_in: int, test_count: int, is_entrypoint: bool, has_callers: bool) -> dict:
    if not has_callers and not is_entrypoint:
        return {"level": "low", "reason": "Nothing internal calls this — likely safe to change or "
                "remove, though it may still be used externally if this is a library."}
    if test_count == 0 and fan_in >= 3:
        return {"level": "high", "reason": f"Called from {fan_in} place{'s' if fan_in != 1 else ''} "
                "with no test coverage found — changing this has a wide, unverified blast radius."}
    if test_count == 0:
        return {"level": "medium", "reason": "No test coverage found for this path — changes won't be "
                "caught automatically, so verify manually."}
    if fan_in >= 6:
        return {"level": "medium", "reason": f"Widely used ({fan_in} direct callers) but has test "
                "coverage — changes are checkable, just review call sites carefully."}
    return {"level": "low", "reason": "Has test coverage and a small, contained set of callers."}


def analyze_impact(session: Session, repo_id: int, symbol_id: int) -> dict:
    sym = session.get(Symbol, symbol_id)
    if sym is None or sym.repo_id != repo_id:
        return {"error": "Symbol not found"}

    nodes = _upstream(session, symbol_id)
    direct = sorted((n for n in nodes.values() if n["depth"] == 1), key=lambda n: n["qualified_name"])
    transitive = sorted((n for n in nodes.values() if n["depth"] > 1),
                         key=lambda n: (n["depth"], n["qualified_name"]))
    test_callers = sorted((n for n in nodes.values() if n["file"].startswith(("tests/", "test/"))),
                           key=lambda n: n["qualified_name"])

    entry_ids = {e["symbol_id"] for e in find_entrypoints(session, repo_id) if e["symbol_id"]}
    is_entrypoint = symbol_id in entry_ids

    coupling = find_change_coupling(session, repo_id, limit=500)
    coupled_files = [p for p in coupling["pairs"] if sym.file_path in (p["a"], p["b"])][:8]

    fan_in = len(direct)
    risk = _risk_verdict(fan_in, len(test_callers), is_entrypoint, fan_in > 0)

    return {
        "symbol": {"id": sym.id, "qualified_name": sym.qualified_name, "kind": sym.kind,
                   "file": sym.file_path, "line": sym.start_line},
        "direct_callers": direct,
        "transitive_callers": transitive,
        "test_callers": test_callers,
        "coupled_files": coupled_files,
        "is_entrypoint": is_entrypoint,
        "fan_in": fan_in,
        "risk": risk,
    }
