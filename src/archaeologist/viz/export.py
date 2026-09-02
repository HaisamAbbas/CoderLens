"""Export the dependency graph as a generic {nodes, links, groups} shape that the
renderer consumes for both the file-level atlas and the symbol-level zoom.

Node schema: {id, label, meta, group, degree, churn, stats:[[label,value],...]}
Link schema: {source, target, weight}
Top level:  {nodes, links, groups:[{key,label}], subtitle, truncated, total_nodes}
"""

from collections import defaultdict

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from archaeologist.models.entities import CommitFile, Symbol, SymbolEdge


def _dirname(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else "(root)"


def _short(module: str) -> str:
    parts = module.split("/")
    return "/".join(parts[-2:]) if len(parts) > 2 else module


def _churn_by_file(session: Session, repo_id: int) -> dict[str, int]:
    """Commits touching each file — same signal Overview's hotspots use, so
    a file that looks risky there looks the same way here."""
    return dict(session.execute(
        select(CommitFile.path, func.count()).where(CommitFile.repo_id == repo_id)
        .group_by(CommitFile.path)
    ).all())


def export_file_graph(
    session: Session, repo_id: int, exclude_tests: bool = False,
    min_weight: int = 1, top_groups: int = 3, max_nodes: int | None = None,
    group_by: str = "dir", community_of: dict[str, int] | None = None,
) -> dict:
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

    total_files = len(files)
    truncated = False
    # A repo with thousands of interconnected files makes the force layout an
    # unreadable hairball no styling fixes — cap to the most-connected files
    # (degree computed from the FULL edge set above, so a kept node's size
    # still reflects its true connectivity, not just connectivity within the
    # trimmed subgraph) rather than let the client try to render everything.
    if max_nodes is not None and total_files > max_nodes:
        files = sorted(files, key=lambda f: -degree[f])[:max_nodes]
        kept = set(files)
        links = [link for link in links if link["source"] in kept and link["target"] in kept]
        files.sort()
        truncated = True

    churn = _churn_by_file(session, repo_id)

    if group_by == "community" and community_of:
        # Community ids come pre-ranked by cluster size (see
        # analysis.communities.community_by_file) — the first `top_groups`
        # get their own color, the rest (including files with no community,
        # id -1) fall back to "other", same shape as directory-mode below.
        top_keys = list(range(top_groups))
        groups = [{"key": str(k), "label": f"Cluster {k + 1}"} for k in top_keys
                  if any(community_of.get(f) == k for f in files)]
        has_other = any(community_of.get(f, -1) not in top_keys for f in files)
        if has_other:
            groups.append({"key": "other", "label": "other"})

        def group_of(f: str) -> str:
            c = community_of.get(f, -1)
            return str(c) if c in top_keys else "other"
    else:
        # Colour by the most-connected directories (summed degree); rest -> 'other'.
        dir_degree: dict[str, int] = defaultdict(int)
        for f in files:
            dir_degree[_dirname(f)] += degree.get(f, 0)
        top_dirs = [d for d, _ in sorted(dir_degree.items(), key=lambda kv: -kv[1])[:top_groups]]
        groups = [{"key": d, "label": _short(d)} for d in top_dirs]
        has_other = any(_dirname(f) not in top_dirs for f in files)
        if has_other:
            groups.append({"key": "other", "label": "other"})

        def group_of(f: str) -> str:
            d = _dirname(f)
            return d if d in top_dirs else "other"

    nodes = []
    for f in files:
        nodes.append({
            "id": f,
            "label": f.rsplit("/", 1)[-1],
            "meta": f,
            "group": group_of(f),
            "degree": degree.get(f, 0),
            "churn": churn.get(f, 0),
            "stats": [["symbols", symbol_count.get(f, 0)], ["connections", degree.get(f, 0)],
                     ["commits touching it", churn.get(f, 0)]],
        })
    subtitle = ("Each node is a file, sized by how connected it is, coloured by module. "
               "Click a file to see what breaks if you remove it.")
    if truncated:
        subtitle += f" Showing the {len(files)} most-connected of {total_files} files."
    return {"nodes": nodes, "links": links, "groups": groups, "subtitle": subtitle,
            "truncated": truncated, "total_nodes": total_files}


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
    total_symbols = len(connected)
    truncated = False
    # Same "no readable hairball" cap as the file graph, for a whole-repo
    # symbol view with no path_prefix to scope it down naturally.
    if not path_prefix and total_symbols > max_nodes:
        connected = set(sorted(connected, key=lambda sid: -degree[sid])[:max_nodes])
        links = [link for link in links if link["source"] in connected and link["target"] in connected]
        truncated = True

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
    subtitle = (f"Symbols in {scope} — calls and inheritance. "
               "Click a symbol to see its callers and callees.")
    if truncated:
        subtitle += f" Showing the {len(connected)} most-connected of {total_symbols} symbols."
    return {"nodes": nodes, "links": links, "groups": groups, "subtitle": subtitle,
            "truncated": truncated, "total_nodes": total_symbols}


def export_combined(session: Session, repo_id: int, exclude_tests: bool = False) -> dict:
    """File-level graph plus the full symbol graph (each symbol tagged with its
    file), so one page can drill from a file down into its symbols."""
    return {
        "files": export_file_graph(session, repo_id, exclude_tests=exclude_tests),
        "symbols": export_symbol_graph(session, repo_id, path_prefix=None),
    }
