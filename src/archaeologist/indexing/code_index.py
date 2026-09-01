"""The code-symbol index in OpenSearch.

Phase 2 gave it BM25 (OpenSearch's default text similarity). Phase 3 adds a
`knn_vector` field so the same index also serves semantic search; `retrieval.hybrid`
fuses the two rankings with RRF.
"""

from opensearchpy import OpenSearch
from opensearchpy.helpers import bulk

from archaeologist.config import settings

SYMBOL_INDEX = "code_symbols"

# Field boosts: a name/qualname match matters more than an incidental code hit.
SEARCH_FIELDS = ["qualified_name^4", "name^3", "signature^2", "docstring^2", "code"]


def _index_body(dim: int) -> dict:
    return {
        "settings": {
            "index": {"number_of_shards": 1, "number_of_replicas": 0, "knn": True}
        },
        "mappings": {
            "properties": {
                "repo_id": {"type": "integer"},
                "symbol_id": {"type": "integer"},
                "file_path": {"type": "keyword"},
                "language": {"type": "keyword"},
                "kind": {"type": "keyword"},
                "name": {"type": "text", "fields": {"kw": {"type": "keyword"}}},
                "qualified_name": {"type": "text", "fields": {"kw": {"type": "keyword"}}},
                "signature": {"type": "text"},
                "docstring": {"type": "text"},
                "code": {"type": "text"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": dim,
                    "method": {
                        "name": "hnsw",
                        "space_type": "cosinesimil",
                        "engine": "lucene",
                    },
                },
            }
        },
    }


def _existing_dim(client: OpenSearch, index_name: str) -> int | None:
    try:
        mapping = client.indices.get_mapping(index=index_name)
        return mapping[index_name]["mappings"]["properties"]["embedding"]["dimension"]
    except Exception:
        return None


def create_index(client: OpenSearch, recreate: bool = False, dim: int | None = None) -> None:
    """Create the index if missing. Only destroys and rebuilds an existing
    index when `recreate` is explicitly requested or the embedding dimension
    genuinely changed (e.g. switching embedding providers) — NOT on every
    routine ingest, which used to wipe every other repo's indexed data every
    single time (`create_index(..., recreate=True)` was called unconditionally
    on every ingest). Per-repo re-indexing goes through `delete_repo_docs`
    instead, so one repo's re-ingest never touches another's documents."""
    dim = dim or settings.embedding_dim
    if client.indices.exists(index=SYMBOL_INDEX):
        if recreate or _existing_dim(client, SYMBOL_INDEX) not in (None, dim):
            client.indices.delete(index=SYMBOL_INDEX)
        else:
            return
    client.indices.create(index=SYMBOL_INDEX, body=_index_body(dim))


def delete_repo_docs(client: OpenSearch, repo_id: int) -> None:
    """Remove just one repo's symbols before re-indexing it — the additive
    counterpart to `create_index`'s no-longer-unconditional wipe."""
    if not client.indices.exists(index=SYMBOL_INDEX):
        return
    client.delete_by_query(
        index=SYMBOL_INDEX, body={"query": {"term": {"repo_id": repo_id}}},
        refresh=True, conflicts="proceed",
    )


def index_documents(client: OpenSearch, docs: list[dict]) -> int:
    """docs must carry `symbol_id`; it is used as the OpenSearch _id."""
    actions = [{"_index": SYMBOL_INDEX, "_id": d["symbol_id"], "_source": d} for d in docs]
    success, _ = bulk(client, actions)
    client.indices.refresh(index=SYMBOL_INDEX)
    return success


def bm25_hits(client: OpenSearch, query: str, k: int, repo_id: int) -> list[tuple[str, dict]]:
    """Raw (id, _source) pairs, ranked, for RRF fusion — scoped to one repo."""
    body = {
        "size": k,
        "query": {
            "bool": {
                "must": [{"multi_match": {"query": query, "fields": SEARCH_FIELDS, "type": "best_fields"}}],
                "filter": [{"term": {"repo_id": repo_id}}],
            }
        },
    }
    resp = client.search(index=SYMBOL_INDEX, body=body)
    return [(h["_id"], h["_source"]) for h in resp["hits"]["hits"]]


def knn_hits(client: OpenSearch, vector: list[float], k: int, repo_id: int) -> list[tuple[str, dict]]:
    """Raw (id, _source) pairs from vector search, ranked, for RRF fusion —
    scoped to one repo via the Lucene engine's native k-NN filter."""
    body = {
        "size": k,
        "query": {"knn": {"embedding": {"vector": vector, "k": k, "filter": {"term": {"repo_id": repo_id}}}}},
    }
    resp = client.search(index=SYMBOL_INDEX, body=body)
    return [(h["_id"], h["_source"]) for h in resp["hits"]["hits"]]


def format_hit(source: dict, score: float) -> dict:
    return {
        "score": score,
        "kind": source["kind"],
        "qualified_name": source["qualified_name"] or source["name"],
        "file_path": source["file_path"],
        "start_line": source["start_line"],
        "end_line": source["end_line"],
        "docstring": source.get("docstring"),
    }


def search(client: OpenSearch, query: str, k: int = 5, kind: str | None = None,
           repo_id: int | None = None) -> list[dict]:
    """BM25-only convenience search (used by the Phase 2 notebook). `repo_id`
    is optional here only because this helper is a direct-DB debug/notebook
    entry point, not a request path — every production caller must pass it."""
    bool_query: dict = {
        "must": [{"multi_match": {"query": query, "fields": SEARCH_FIELDS, "type": "best_fields"}}]
    }
    filters = []
    if kind:
        filters.append({"term": {"kind": kind}})
    if repo_id is not None:
        filters.append({"term": {"repo_id": repo_id}})
    if filters:
        bool_query["filter"] = filters
    resp = client.search(index=SYMBOL_INDEX, body={"size": k, "query": {"bool": bool_query}})
    return [format_hit(h["_source"], h["_score"]) for h in resp["hits"]["hits"]]
