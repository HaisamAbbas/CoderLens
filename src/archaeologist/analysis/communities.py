"""Community detection over the call graph — functional clusters discovered
from how symbols actually reference each other, which don't always line up
with folder structure (a class and its two helper mixins in different files
can cluster together; two files in the same folder can end up in different
clusters if they don't actually talk to each other).

Uses networkx's greedy-modularity partitioning: same purpose as the Leiden
algorithm (maximize modularity — dense internal connections, sparse between
clusters) without pulling in igraph's native build. Edges are weighted by
confidence (indexing/graph.py) so ambiguous name-matches barely pull symbols
together, while resolved calls do.
"""

from collections import Counter, defaultdict

import networkx as nx
from sqlalchemy import select
from sqlalchemy.orm import Session

from archaeologist.models.entities import Symbol, SymbolEdge


def find_communities(session: Session, repo_id: int, min_size: int = 3, limit: int = 12) -> dict:
    symbols = {
        s.id: s for s in session.scalars(
            select(Symbol).where(Symbol.repo_id == repo_id,
                                  Symbol.kind.in_(("class", "method", "function")),
                                  ~Symbol.file_path.like("tests/%"))
        ).all()
    }
    if not symbols:
        return {"clusters": [], "total": 0}

    g = nx.Graph()
    g.add_nodes_from(symbols)
    for src, dst, conf in session.execute(
        select(SymbolEdge.src_symbol_id, SymbolEdge.dst_symbol_id, SymbolEdge.confidence)
        .where(SymbolEdge.repo_id == repo_id, SymbolEdge.dst_symbol_id.is_not(None))
    ):
        if src not in symbols or dst not in symbols or src == dst:
            continue
        if g.has_edge(src, dst):
            g[src][dst]["weight"] += conf
        else:
            g.add_edge(src, dst, weight=conf)

    g.remove_nodes_from(list(nx.isolates(g)))  # no internal edges → not a cluster
    if g.number_of_nodes() < min_size:
        return {"clusters": [], "total": 0}

    raw = nx.algorithms.community.greedy_modularity_communities(g, weight="weight")

    clusters = []
    for members in raw:
        if len(members) < min_size:
            continue
        syms = [symbols[m] for m in members]
        dirs = Counter(s.file_path.rsplit("/", 1)[0] if "/" in s.file_path else "(root)" for s in syms)
        files = Counter(s.file_path for s in syms)
        classes = [s for s in syms if s.kind == "class"]
        label = (max(classes, key=lambda s: g.degree(s.id, weight="weight")).name if classes
                 else files.most_common(1)[0][0].rsplit("/", 1)[-1])
        ranked = sorted(syms, key=lambda s: -g.degree(s.id, weight="weight"))
        clusters.append({
            "label": label,
            "size": len(syms),
            "primary_dir": dirs.most_common(1)[0][0],
            "dir_spread": len(dirs),
            "members": [
                {"id": s.id, "qualified_name": s.qualified_name, "kind": s.kind,
                 "path": s.file_path, "line": s.start_line}
                for s in ranked[:20]
            ],
        })

    clusters.sort(key=lambda c: -c["size"])
    return {"clusters": clusters[:limit], "total": len(clusters)}


def community_by_file(session: Session, repo_id: int, min_size: int = 3) -> dict[str, int]:
    """File -> community rank (0 = largest cluster), for coloring the
    dependency graph by real call-structure clusters instead of folder
    names. Deliberately separate from find_communities rather than sharing
    its truncated (top `limit` clusters, `20`-member cap) output: every
    file needs an answer here, not just the handful of clusters big enough
    to show on the Communities page. Some duplication of the graph-building
    step below is the tradeoff for never risking that page's behavior.

    A file can have symbols split across more than one cluster (e.g. a
    module with two loosely related classes) — it's assigned to whichever
    cluster holds the most of its own symbols, a simple majority vote.
    """
    symbols = {
        s.id: s for s in session.scalars(
            select(Symbol).where(Symbol.repo_id == repo_id,
                                  Symbol.kind.in_(("class", "method", "function")),
                                  ~Symbol.file_path.like("tests/%"))
        ).all()
    }
    if not symbols:
        return {}

    g = nx.Graph()
    g.add_nodes_from(symbols)
    for src, dst, conf in session.execute(
        select(SymbolEdge.src_symbol_id, SymbolEdge.dst_symbol_id, SymbolEdge.confidence)
        .where(SymbolEdge.repo_id == repo_id, SymbolEdge.dst_symbol_id.is_not(None))
    ):
        if src not in symbols or dst not in symbols or src == dst:
            continue
        if g.has_edge(src, dst):
            g[src][dst]["weight"] += conf
        else:
            g.add_edge(src, dst, weight=conf)

    g.remove_nodes_from(list(nx.isolates(g)))
    if g.number_of_nodes() < min_size:
        return {}

    raw = nx.algorithms.community.greedy_modularity_communities(g, weight="weight")
    clusters = sorted((c for c in raw if len(c) >= min_size), key=len, reverse=True)

    file_votes: dict[str, Counter] = defaultdict(Counter)
    for rank, members in enumerate(clusters):
        for sid in members:
            file_votes[symbols[sid].file_path][rank] += 1

    return {path: votes.most_common(1)[0][0] for path, votes in file_votes.items()}
