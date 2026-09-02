"""Tools the investigation agent can call: cross-stream search and graph expansion."""

from archaeologist.indexing.opensearch_client import get_client
from archaeologist.models.db import session_scope
from archaeologist.retrieval.embeddings import get_embedder
from archaeologist.retrieval.graph_queries import call_path, find_symbol, who_depends_on
from archaeologist.retrieval.multi import search_all

# Reused across nodes so we don't rebuild the embedder (model load) each call.
_client = None
_embedder = None


def _clients():
    global _client, _embedder
    if _client is None:
        _client = get_client()
        _embedder = get_embedder()
    return _client, _embedder


def search(queries: list[str], streams: list[str] | None, repo_id: int, k: int = 6) -> list[dict]:
    client, embedder = _clients()
    hits: list[dict] = []
    for query in queries:
        hits.extend(search_all(client, embedder, query, repo_id, k=k, streams=streams))
    return hits


def graph_expand(qualified_names: list[str], repo_id: int) -> list[dict]:
    """Turn dependency-graph facts into evidence items (stream='graph')."""
    out: list[dict] = []
    with session_scope() as session:
        for qn in qualified_names:
            sym = find_symbol(session, qn, repo_id)
            if sym is None:
                continue
            dependents = who_depends_on(session, sym.id, repo_id)
            outgoing = call_path(session, sym.id, repo_id, max_depth=2)

            breaks = ", ".join(f"{et}:{s.qualified_name}" for et, s in dependents[:15]) or "(none internal)"
            calls = ", ".join(s.qualified_name for d, s in outgoing[1:16]) or "(none resolved)"
            body = (
                f"Symbol {qn} ({sym.file_path}:{sym.start_line}).\n"
                f"What breaks if removed (callers/subclasses): {breaks}\n"
                f"Outgoing calls (execution path): {calls}"
            )
            out.append({
                "stream": "graph",
                "title": qn,
                "citation": f"{sym.file_path}:{sym.start_line}",
                "snippet": body[:200],
                "body": body,
                # Lets the UI jump straight to this symbol's exact line, the
                # same way a "code" hit does — without these the citation
                # wasn't openable at all.
                "symbol_id": sym.id,
                "path": sym.file_path,
            })
    return out
