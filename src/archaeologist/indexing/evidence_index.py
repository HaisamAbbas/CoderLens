"""The unified 'evidence' index (docs + commits + issues) in OpenSearch.

Same BM25 + knn_vector shape as the code index, but with a `stream` field so
cross-stream search can filter by stream and cite each hit correctly.
"""

from opensearchpy import OpenSearch
from opensearchpy.helpers import bulk

from archaeologist.config import settings

EVIDENCE_INDEX = "evidence"
SEARCH_FIELDS = ["title^2", "text"]


def _index_body(dim: int) -> dict:
    return {
        "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0, "knn": True}},
        "mappings": {
            "properties": {
                "stream": {"type": "keyword"},
                "ref_id": {"type": "keyword"},
                "title": {"type": "text"},
                "text": {"type": "text"},
                "citation": {"type": "keyword"},
                "file_path": {"type": "keyword"},
                "sha": {"type": "keyword"},
                "number": {"type": "integer"},
                "state": {"type": "keyword"},
                "kind": {"type": "keyword"},
                "author": {"type": "keyword"},
                "date": {"type": "keyword"},
                "url": {"type": "keyword"},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": dim,
                    "method": {"name": "hnsw", "space_type": "cosinesimil", "engine": "lucene"},
                },
            }
        },
    }


def create_index(client: OpenSearch, recreate: bool = True, dim: int | None = None) -> None:
    if client.indices.exists(index=EVIDENCE_INDEX):
        if not recreate:
            return
        client.indices.delete(index=EVIDENCE_INDEX)
    client.indices.create(index=EVIDENCE_INDEX, body=_index_body(dim or settings.embedding_dim))


def index_documents(client: OpenSearch, docs: list[dict]) -> int:
    actions = [{"_index": EVIDENCE_INDEX, "_id": f"{d['stream']}:{d['ref_id']}", "_source": d}
               for d in docs]
    success, _ = bulk(client, actions)
    client.indices.refresh(index=EVIDENCE_INDEX)
    return success


def _wrap(query_clause: dict, streams: list[str] | None) -> dict:
    if streams:
        return {"bool": {"must": [query_clause], "filter": [{"terms": {"stream": streams}}]}}
    return query_clause


def bm25_hits(client, query: str, k: int, streams: list[str] | None = None) -> list[tuple[str, dict]]:
    clause = {"multi_match": {"query": query, "fields": SEARCH_FIELDS, "type": "best_fields"}}
    body = {"size": k, "query": _wrap(clause, streams)}
    resp = client.search(index=EVIDENCE_INDEX, body=body)
    return [(h["_id"], h["_source"]) for h in resp["hits"]["hits"]]


def knn_hits(client, vector: list[float], k: int, streams: list[str] | None = None) -> list[tuple[str, dict]]:
    clause = {"knn": {"embedding": {"vector": vector, "k": k}}}
    body = {"size": k, "query": _wrap(clause, streams)}
    resp = client.search(index=EVIDENCE_INDEX, body=body)
    return [(h["_id"], h["_source"]) for h in resp["hits"]["hits"]]
