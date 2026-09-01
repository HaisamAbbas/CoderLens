"""Phase 4b CLI: build the unified evidence index (docs + commits + issues).

    uv run python -m archaeologist.indexing.streams_run
    uv run python -m archaeologist.indexing.streams_run --query "why was async support added"
"""

import argparse

from sqlalchemy import select

from archaeologist.config import settings
from archaeologist.indexing import evidence_index, streams
from archaeologist.indexing.opensearch_client import get_client
from archaeologist.models.db import init_db, session_scope
from archaeologist.models.entities import Repo
from archaeologist.retrieval.embeddings import get_embedder


def _embed_text(doc: dict) -> str:
    return f"{doc.get('title', '')}\n{(doc.get('text') or '')[:2000]}"


def build_evidence_index(embed: bool = True, repo_id: int | None = None) -> int:
    """Build the unified evidence index. `repo_id` targets a specific repo
    (used by the web ingestion job); when omitted, the most recently ingested
    one is used."""
    init_db()
    client = get_client()
    embedder = get_embedder() if embed else None
    dim = embedder.dim if embedder is not None else settings.embedding_dim

    with session_scope() as session:
        repo = (session.get(Repo, repo_id) if repo_id is not None
                else session.scalar(select(Repo).order_by(Repo.id.desc())))
        if repo is None:
            raise SystemExit("No repo ingested — run Phase 1 first.")
        docs = streams.build_evidence_docs(session, repo.id)

    by_stream: dict[str, int] = {}
    for d in docs:
        by_stream[d["stream"]] = by_stream.get(d["stream"], 0) + 1
    print(f"[1/2] Built {len(docs)} evidence docs: {by_stream}")

    # Only fully (re)creates the index on first use or a real dimension
    # change, then wipes just THIS repo's old docs — not the whole shared
    # index (that used to happen on every single ingest of any repo).
    evidence_index.create_index(client, dim=dim)
    evidence_index.delete_repo_docs(client, repo.id)
    if embedder is not None:
        print(f"      embedding {len(docs)} docs ({settings.embedding_provider}, dim={dim}) ...")
        vectors = embedder.embed_documents([_embed_text(d) for d in docs])
        for doc, vec in zip(docs, vectors):
            doc["embedding"] = vec
    else:
        print("      (no embedder -> BM25 only, no vectors)")

    n = evidence_index.index_documents(client, docs) if docs else 0
    print(f"[2/2] Indexed {n} docs into '{evidence_index.EVIDENCE_INDEX}'.")
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the evidence index (Phase 4b).")
    parser.add_argument("--no-embeddings", action="store_true")
    parser.add_argument("--query", help="After indexing, run a cross-stream search")
    parser.add_argument("--k", type=int, default=8)
    args = parser.parse_args()

    build_evidence_index(embed=not args.no_embeddings)

    if args.query:
        from archaeologist.retrieval.multi import search_all

        client = get_client()
        embedder = get_embedder()
        with session_scope() as session:
            repo_id = session.scalar(select(Repo).order_by(Repo.id.desc())).id
        print(f"\n=== cross-stream search: {args.query!r} ===")
        for hit in search_all(client, embedder, args.query, k=args.k, repo_id=repo_id):
            print(f"  {hit['score']:.4f}  [{hit['stream']:6}] {hit['citation']:24.24} {hit['title'][:48]}")


if __name__ == "__main__":
    main()
