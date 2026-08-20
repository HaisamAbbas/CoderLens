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


def create_index(client: OpenSearch, recreate: bool = True, dim: int | None = None) -> None:
    if client.indices.exists(index=SYMBOL_INDEX):
        if not recreate:
            return
        client.indices.delete(index=SYMBOL_INDEX)
    client.indices.create(index=SYMBOL_INDEX, body=_index_body(dim or settings.embedding_dim))


def index_documents(client: OpenSearch, docs: list[dict]) -> int:
    """docs must carry `symbol_id`; it is used as the OpenSearch _id."""
    actions = [{"_index": SYMBOL_INDEX, "_id": d["symbol_id"], "_source": d} for d in docs]
    success, _ = bulk(client, actions)
    client.indices.refresh(index=SYMBOL_INDEX)
    return success


def bm25_hits(client: OpenSearch, query: str, k: int) -> list[tuple[str, dict]]:
    """Raw (id, _source) pairs, ranked, for RRF fusion."""
    body = {
        "size": k,
        "query": {"multi_match": {"query": query, "fields": SEARCH_FIELDS, "type": "best_fields"}},
    }
    resp = client.search(index=SYMBOL_INDEX, body=body)
    return [(h["_id"], h["_source"]) for h in resp["hits"]["hits"]]


def knn_hits(client: OpenSearch, vector: list[float], k: int) -> list[tuple[str, dict]]:
    """Raw (id, _source) pairs from vector search, ranked, for RRF fusion."""
    body = {"size": k, "query": {"knn": {"embedding": {"vector": vector, "k": k}}}}
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


def search(client: OpenSearch, query: str, k: int = 5, kind: str | None = None) -> list[dict]:
    """BM25-only convenience search (used by the Phase 2 notebook)."""
    bool_query: dict = {
        "must": [{"multi_match": {"query": query, "fields": SEARCH_FIELDS, "type": "best_fields"}}]
    }
    if kind:
        bool_query["filter"] = [{"term": {"kind": kind}}]
    resp = client.search(index=SYMBOL_INDEX, body={"size": k, "query": {"bool": bool_query}})
    return [format_hit(h["_source"], h["_score"]) for h in resp["hits"]["hits"]]
