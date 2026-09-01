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
                "repo_id": {"type": "integer"},
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


def _existing_dim(client: OpenSearch, index_name: str) -> int | None:
    try:
        mapping = client.indices.get_mapping(index=index_name)
        return mapping[index_name]["mappings"]["properties"]["embedding"]["dimension"]
    except Exception:
        return None


def create_index(client: OpenSearch, recreate: bool = False, dim: int | None = None) -> None:
    """See code_index.create_index's docstring — same fix: only destroys and
    rebuilds on an explicit request or a real dimension change, not on every
    routine ingest (which used to wipe every other repo's evidence docs)."""
    dim = dim or settings.embedding_dim
    if client.indices.exists(index=EVIDENCE_INDEX):
        if recreate or _existing_dim(client, EVIDENCE_INDEX) not in (None, dim):
            client.indices.delete(index=EVIDENCE_INDEX)
        else:
            return
    client.indices.create(index=EVIDENCE_INDEX, body=_index_body(dim))


def delete_repo_docs(client: OpenSearch, repo_id: int) -> None:
    """Remove just one repo's evidence docs before re-indexing it."""
    if not client.indices.exists(index=EVIDENCE_INDEX):
        return
    client.delete_by_query(
        index=EVIDENCE_INDEX, body={"query": {"term": {"repo_id": repo_id}}},
        refresh=True, conflicts="proceed",
    )


def index_documents(client: OpenSearch, docs: list[dict]) -> int:
    """docs must carry `repo_id` — folded into the _id (not just `stream:ref_id`)
    because ref_id isn't globally unique: issue numbers restart at 1 per repo
    and doc paths like "README.md" repeat across repos, so without repo_id two
    different repos' evidence could silently overwrite each other's documents
    in this shared index."""
    actions = [{"_index": EVIDENCE_INDEX, "_id": f"{d['repo_id']}:{d['stream']}:{d['ref_id']}", "_source": d}
               for d in docs]
    success, _ = bulk(client, actions)
    client.indices.refresh(index=EVIDENCE_INDEX)
    return success


def _wrap(query_clause: dict, repo_id: int, streams: list[str] | None) -> dict:
    filters = [{"term": {"repo_id": repo_id}}]
    if streams:
        filters.append({"terms": {"stream": streams}})
    return {"bool": {"must": [query_clause], "filter": filters}}


def bm25_hits(client, query: str, k: int, repo_id: int, streams: list[str] | None = None) -> list[tuple[str, dict]]:
    clause = {"multi_match": {"query": query, "fields": SEARCH_FIELDS, "type": "best_fields"}}
    body = {"size": k, "query": _wrap(clause, repo_id, streams)}
    resp = client.search(index=EVIDENCE_INDEX, body=body)
    return [(h["_id"], h["_source"]) for h in resp["hits"]["hits"]]


def knn_hits(client, vector: list[float], k: int, repo_id: int, streams: list[str] | None = None) -> list[tuple[str, dict]]:
    clause = {"knn": {"embedding": {"vector": vector, "k": k}}}
    body = {"size": k, "query": _wrap(clause, repo_id, streams)}
    resp = client.search(index=EVIDENCE_INDEX, body=body)
    return [(h["_id"], h["_source"]) for h in resp["hits"]["hits"]]
