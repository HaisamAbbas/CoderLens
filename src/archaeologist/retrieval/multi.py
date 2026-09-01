"""Cross-stream retrieval — fuse the code index and the evidence index
(docs/commits/issues) into one ranked, citable evidence list.

Each hit is normalized to {stream, title, citation, snippet, score} so the
agent (Phase 6) can treat all five streams uniformly.
"""

from archaeologist.indexing import code_index, evidence_index
from archaeologist.retrieval.hybrid import rrf

ALL_STREAMS = ["code", "doc", "commit", "issue"]
EVIDENCE_STREAMS = {"doc", "commit", "issue"}


def _norm_code(source: dict) -> dict:
    body = source.get("code") or source.get("docstring") or ""
    return {
        "stream": "code",
        "title": source["qualified_name"] or source["name"],
        "citation": f"{source['file_path']}:{source['start_line']}",
        "snippet": (source.get("docstring") or source.get("code") or "")[:200].strip(),
        "body": body[:1500].strip(),
        # Extra keys the UI uses to jump straight from a citation to the symbol.
        "symbol_id": source.get("symbol_id"),
        "path": source.get("file_path"),
    }


def _norm_evidence(source: dict) -> dict:
    text = source.get("text") or ""
    return {
        "stream": source["stream"],
        "title": source.get("title") or "",
        "citation": source.get("citation") or "",
        "snippet": text[:200].strip(),
        "body": text[:1500].strip(),
        # Lets the UI jump from a doc hit straight to the file.
        "path": source.get("file_path"),
    }


def search_all(client, embedder, query: str, repo_id: int, k: int = 8, candidates: int = 15,
               streams: list[str] | None = None) -> list[dict]:
    """Fused search across streams, scoped to one repo — `repo_id` is
    required (no default) so a caller can't accidentally search the whole
    shared, multi-repo index. `streams` filters to a subset (default all)."""
    want = set(streams) if streams else set(ALL_STREAMS)
    vector = embedder.embed_query(query) if embedder is not None else None
    rankings: list[list[tuple[str, dict]]] = []

    if "code" in want:
        rankings.append([(cid, _norm_code(src)) for cid, src in
                         code_index.bm25_hits(client, query, candidates, repo_id)])
        if vector is not None:
            rankings.append([(cid, _norm_code(src)) for cid, src in
                             code_index.knn_hits(client, vector, candidates, repo_id)])

    evidence_streams = [s for s in want if s in EVIDENCE_STREAMS]
    if evidence_streams:
        rankings.append([(eid, _norm_evidence(src)) for eid, src in
                         evidence_index.bm25_hits(client, query, candidates, repo_id, evidence_streams)])
        if vector is not None:
            rankings.append([(eid, _norm_evidence(src)) for eid, src in
                             evidence_index.knn_hits(client, vector, candidates, repo_id, evidence_streams)])

    results = []
    for _id, score, source in rrf(rankings)[:k]:
        hit = dict(source)
        hit["score"] = score
        results.append(hit)
    return results
