"""Hybrid retrieval: fuse BM25 and semantic rankings with Reciprocal Rank Fusion.

RRF combines rankings by rank position, not raw score, so BM25's TF-IDF scale
and cosine similarity don't need normalizing. If no embedder is available it
degrades to BM25-only (a single ranking through the same fusion).
"""

from archaeologist.indexing import code_index

RRF_K = 60  # standard RRF constant


def rrf(result_lists: list[list[tuple[str, dict]]], k: int = RRF_K) -> list[tuple[str, float, dict]]:
    """Fuse ranked (id, source) lists into one ranking by summed 1/(k+rank)."""
    scores: dict[str, float] = {}
    sources: dict[str, dict] = {}
    for results in result_lists:
        for rank, (doc_id, source) in enumerate(results):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
            sources[doc_id] = source
    ordered = sorted(scores, key=lambda i: scores[i], reverse=True)
    return [(doc_id, scores[doc_id], sources[doc_id]) for doc_id in ordered]


def hybrid_search(client, embedder, query: str, k: int = 5, candidates: int = 20) -> list[dict]:
    """Return top-k fused hits. `embedder` may be None (BM25-only)."""
    rankings = [code_index.bm25_hits(client, query, candidates)]
    if embedder is not None:
        vector = embedder.embed_query(query)
        rankings.append(code_index.knn_hits(client, vector, candidates))

    fused = rrf(rankings)[:k]
    return [code_index.format_hit(source, score) for _id, score, source in fused]
