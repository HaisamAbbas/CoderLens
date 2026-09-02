"""Dependency-graph queries over symbol_edges — the reasoning the archaeologist
does on top of retrieval: reverse dependencies, coupling, and execution paths.
"""

from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from archaeologist.models.entities import Symbol, SymbolEdge


def find_symbol(session: Session, qualified_name: str, repo_id: int) -> Symbol | None:
    return session.scalar(
        select(Symbol).where(Symbol.qualified_name == qualified_name, Symbol.repo_id == repo_id)
    )


def who_depends_on(session: Session, symbol_id: int, repo_id: int) -> list[tuple[str, Symbol]]:
    """Symbols that call or inherit the given symbol — 'what breaks if I remove X'.

    `repo_id` scopes both the edge and the joined symbol — every caller
    today already resolves `symbol_id` through a repo/ownership check of its
    own, but this closes the query itself rather than depending solely on
    caller discipline (see the identical note on most_coupled_files above)."""
    rows = session.execute(
        select(SymbolEdge.edge_type, Symbol)
        .join(Symbol, Symbol.id == SymbolEdge.src_symbol_id)
        .where(SymbolEdge.dst_symbol_id == symbol_id,
               SymbolEdge.repo_id == repo_id, Symbol.repo_id == repo_id)
        .order_by(SymbolEdge.edge_type)
    )
    return [(edge_type, sym) for edge_type, sym in rows]


def most_coupled_files(
    session: Session, repo_id: int, limit: int = 10, exclude_tests: bool = False
) -> list[tuple[str, str, int]]:
    """File pairs with the most cross-file symbol edges (tight coupling).
    `exclude_tests` drops test files, which otherwise dominate (they call everything)."""
    src = aliased(Symbol)
    dst = aliased(Symbol)
    query = (
        select(src.file_path, dst.file_path, func.count().label("n"))
        .select_from(SymbolEdge)
        .join(src, src.id == SymbolEdge.src_symbol_id)
        .join(dst, dst.id == SymbolEdge.dst_symbol_id)
        .where(src.file_path != dst.file_path,
               # `repo_id` is a required parameter but previously never
               # applied — this aggregate spanned symbol_edges for every
               # repo of every user in the shared table.
               SymbolEdge.repo_id == repo_id, src.repo_id == repo_id, dst.repo_id == repo_id)
    )
    if exclude_tests:
        query = query.where(~src.file_path.like("tests/%"), ~dst.file_path.like("tests/%"))
    query = query.group_by(src.file_path, dst.file_path).order_by(func.count().desc()).limit(limit)
    return [(a, b, n) for a, b, n in session.execute(query)]


def call_flow(session: Session, start_symbol_id: int, repo_id: int, depth: int = 3,
              fanout: int = 8, max_nodes: int = 48) -> dict:
    """A layered call-flow graph downstream of a symbol (nodes + edges), for the
    flowchart view. Breadth is capped per node and overall to stay readable.

    `repo_id` scopes every edge/symbol traversed — see who_depends_on's note."""
    start = session.get(Symbol, start_symbol_id)
    if start is None or start.repo_id != repo_id:
        return {"root": start_symbol_id, "nodes": [], "edges": []}

    def node(sym: Symbol, d: int) -> dict:
        return {"id": sym.id, "qualified_name": sym.qualified_name, "name": sym.name,
                "kind": sym.kind, "file": sym.file_path, "line": sym.start_line, "depth": d}

    nodes: dict[int, dict] = {start.id: node(start, 0)}
    edges: list[dict] = []
    seen_edge: set[tuple[int, int]] = set()
    frontier = [start.id]

    for d in range(1, depth + 1):
        if len(nodes) >= max_nodes:
            break
        rows = session.execute(
            select(SymbolEdge.src_symbol_id, SymbolEdge.confidence, Symbol)
            .join(Symbol, Symbol.id == SymbolEdge.dst_symbol_id)
            .where(SymbolEdge.src_symbol_id.in_(frontier),
                   SymbolEdge.edge_type == "call",
                   SymbolEdge.dst_symbol_id.is_not(None),
                   SymbolEdge.repo_id == repo_id, Symbol.repo_id == repo_id)
            # Prefer confidently-resolved calls when the per-node fanout cap kicks in,
            # so a real dependency doesn't get crowded out by an ambiguous name match.
            .order_by(SymbolEdge.confidence.desc())
        ).all()
        per_src: dict[int, int] = defaultdict(int)
        next_frontier: list[int] = []
        for src, conf, sym in rows:
            if src == sym.id or per_src[src] >= fanout:
                continue
            key = (src, sym.id)
            if key in seen_edge:
                continue
            per_src[src] += 1
            seen_edge.add(key)
            edges.append({"source": src, "target": sym.id, "confidence": conf})
            if sym.id not in nodes and len(nodes) < max_nodes:
                nodes[sym.id] = node(sym, d)
                next_frontier.append(sym.id)
        frontier = next_frontier

    node_ids = set(nodes)
    edges = [e for e in edges if e["source"] in node_ids and e["target"] in node_ids]
    return {"root": start.id, "nodes": list(nodes.values()), "edges": edges}


def call_path(session: Session, start_symbol_id: int, repo_id: int, max_depth: int = 4) -> list[tuple[int, Symbol]]:
    """BFS over outgoing `call` edges from a symbol — an execution-path sketch.
    Returns (depth, symbol) in discovery order. `repo_id` scopes the walk —
    see who_depends_on's note."""
    visited = {start_symbol_id}
    start = session.get(Symbol, start_symbol_id)
    if start is not None and start.repo_id != repo_id:
        start = None
    result: list[tuple[int, Symbol]] = [(0, start)] if start else []
    frontier = [start_symbol_id]

    for depth in range(1, max_depth + 1):
        if not frontier:
            break
        rows = session.execute(
            select(SymbolEdge.dst_symbol_id, Symbol)
            .join(Symbol, Symbol.id == SymbolEdge.dst_symbol_id)
            .where(
                SymbolEdge.src_symbol_id.in_(frontier),
                SymbolEdge.edge_type == "call",
                SymbolEdge.dst_symbol_id.is_not(None),
                SymbolEdge.repo_id == repo_id, Symbol.repo_id == repo_id,
            )
        ).all()
        next_frontier: list[int] = []
        for dst_id, sym in rows:
            if dst_id not in visited:
                visited.add(dst_id)
                result.append((depth, sym))
                next_frontier.append(dst_id)
        frontier = next_frontier
    return result
