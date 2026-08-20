"""Export the dependency graph as a generic {nodes, links, groups} shape that the
renderer consumes for both the file-level atlas and the symbol-level zoom.

Node schema: {id, label, meta, group, degree, stats:[[label,value],...]}
Link schema: {source, target, weight}
Top level:  {nodes, links, groups:[{key,label}], subtitle}
"""

from collections import defaultdict

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from archaeologist.models.entities import Symbol, SymbolEdge


def _dirname(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else "(root)"


def _short(module: str) -> str:
    parts = module.split("/")
    return "/".join(parts[-2:]) if len(parts) > 2 else module


def export_file_graph(session: Session, repo_id: int, exclude_tests: bool = False,
                      min_weight: int = 1, top_groups: int = 3) -> dict:
    symbols = {s.id: s for s in session.scalars(select(Symbol).where(Symbol.repo_id == repo_id))}

    symbol_count: dict[str, int] = defaultdict(int)
    for s in symbols.values():
        if s.kind != "import":
            symbol_count[s.file_path] += 1

    weights: dict[tuple[str, str], int] = defaultdict(int)
    for e in session.scalars(select(SymbolEdge).where(
            SymbolEdge.repo_id == repo_id, SymbolEdge.dst_symbol_id.is_not(None))):
        src, dst = symbols.get(e.src_symbol_id), symbols.get(e.dst_symbol_id)
        if not src or not dst or src.file_path == dst.file_path:
            continue
        if exclude_tests and (src.file_path.startswith("tests/") or dst.file_path.startswith("tests/")):
            continue
        weights[(src.file_path, dst.file_path)] += 1

    links = [{"source": a, "target": b, "weight": w}
             for (a, b), w in weights.items() if w >= min_weight]

    degree: dict[str, int] = defaultdict(int)
    for link in links:
        degree[link["source"]] += link["weight"]
        degree[link["target"]] += link["weight"]
    files = sorted({link["source"] for link in links} | {link["target"] for link in links})

    # Colour by the most-connected directories (summed degree); rest -> 'other'.
    dir_degree: dict[str, int] = defaultdict(int)
    for f in files:
        dir_degree[_dirname(f)] += degree.get(f, 0)
    top_dirs = [d for d, _ in sorted(dir_degree.items(), key=lambda kv: -kv[1])[:top_groups]]
    groups = [{"key": d, "label": _short(d)} for d in top_dirs]
    has_other = any(_dirname(f) not in top_dirs for f in files)
    if has_other:
        groups.append({"key": "other", "label": "other"})

    nodes = []
    for f in files:
        d = _dirname(f)
        nodes.append({
            "id": f,
            "label": f.rsplit("/", 1)[-1],
            "meta": f,
            "group": d if d in top_dirs else "other",
            "degree": degree.get(f, 0),
            "stats": [["symbols", symbol_count.get(f, 0)], ["connections", degree.get(f, 0)]],
        })
    return {"nodes": nodes, "links": links, "groups": groups,
            "subtitle": "Each node is a file, sized by how connected it is, coloured by module. "
                        "Click a file to see what breaks if you remove it."}


def export_symbol_graph(session: Session, repo_id: int, path_prefix: str | None = None,
                        include_neighbors: bool = False, max_nodes: int = 90) -> dict:
    kinds = ["class", "method", "function"]
    q = select(Symbol).where(Symbol.repo_id == repo_id, Symbol.kind.in_(kinds))
    if path_prefix:
        q = q.where(Symbol.file_path.like(f"{path_prefix}%"))
    symbols = {s.id: s for s in session.scalars(q)}
    ids = set(symbols)

    # For a scoped view, add the most strongly-connected 1-hop neighbors so
    # cross-file calls are visible — capped so a hub file stays readable.
    if path_prefix and include_neighbors and ids:
        edge_rows = session.execute(
            select(SymbolEdge.src_symbol_id, SymbolEdge.dst_symbol_id).where(
                SymbolEdge.repo_id == repo_id, SymbolEdge.dst_symbol_id.is_not(None),
                or_(SymbolEdge.src_symbol_id.in_(ids), SymbolEdge.dst_symbol_id.in_(ids)))
        ).all()
        strength: dict[int, int] = defaultdict(int)
        for src, dst in edge_rows:
            if src in ids and dst not in ids:
                strength[dst] += 1
            elif dst in ids and src not in ids:
                strength[src] += 1
        budget = max(0, max_nodes - len(ids))
        top = [nid for nid, _ in sorted(strength.items(), key=lambda kv: -kv[1])[:budget]]
        if top:
            for s in session.scalars(select(Symbol).where(
                    Symbol.id.in_(top), Symbol.kind.in_(kinds))):
                symbols[s.id] = s
            ids = set(symbols)

    links: list[dict] = []
    degree: dict[int, int] = defaultdict(int)
    for e in session.scalars(select(SymbolEdge).where(
            SymbolEdge.repo_id == repo_id, SymbolEdge.dst_symbol_id.is_not(None))):
        if e.src_symbol_id in ids and e.dst_symbol_id in ids:
            links.append({"source": e.src_symbol_id, "target": e.dst_symbol_id, "weight": 1})
            degree[e.src_symbol_id] += 1
            degree[e.dst_symbol_id] += 1

    connected = {link["source"] for link in links} | {link["target"] for link in links}
    nodes = []
    for sid in connected:
        s = symbols[sid]
        nodes.append({
            "id": sid,
            "label": s.qualified_name,
            "meta": f"{s.file_path}:{s.start_line}",
            "file": s.file_path,
            "group": s.kind,
            "degree": degree.get(sid, 0),
            "stats": [["kind", s.kind], ["connections", degree.get(sid, 0)]],
        })

    groups = [{"key": "class", "label": "Classes"},
              {"key": "method", "label": "Methods"},
              {"key": "function", "label": "Functions"}]
    scope = path_prefix or "the whole repo"
    return {"nodes": nodes, "links": links, "groups": groups,
            "subtitle": f"Symbols in {scope} — calls and inheritance. "
                        "Click a symbol to see its callers and callees."}


def export_combined(session: Session, repo_id: int, exclude_tests: bool = False) -> dict:
    """File-level graph plus the full symbol graph (each symbol tagged with its
    file), so one page can drill from a file down into its symbols."""
    return {
        "files": export_file_graph(session, repo_id, exclude_tests=exclude_tests),
        "symbols": export_symbol_graph(session, repo_id, path_prefix=None),
    }
