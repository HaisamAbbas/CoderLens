"""Phase 2 indexing CLI: extract symbols from ingested Python files into Postgres,
then build the BM25 code index in OpenSearch.

    uv run python -m archaeologist.indexing.run
    uv run python -m archaeologist.indexing.run --query "where is url routing dispatched"
"""

import argparse

from sqlalchemy import delete, insert, select, update

from archaeologist.config import settings
from archaeologist.indexing import code_index, languages, symbols
from archaeologist.indexing.opensearch_client import get_client
from archaeologist.models.db import init_db, session_scope
from archaeologist.models.entities import File, Repo, Symbol, SymbolEdge, Weakness
from archaeologist.retrieval.embeddings import get_embedder


def _embedding_text(doc: dict) -> str:
    """What we embed for a symbol: identity + docstring + a bounded slice of source."""
    parts = [doc["qualified_name"] or doc["name"], doc.get("signature") or "",
             doc.get("docstring") or "", (doc.get("code") or "")[:500]]
    return "\n".join(p for p in parts if p)


def extract_to_postgres(repo_id: int | None = None) -> int:
    """Parse every ingested code file in a supported language (python,
    javascript, typescript/tsx, go) into Symbol rows. Returns symbol count.

    `repo_id` targets a specific repo (used by the web ingestion job); when
    omitted it uses the most recently ingested one.
    """
    init_db()
    total = 0
    with session_scope() as session:
        repo = _pick_repo(session, repo_id)
        if repo is None:
            raise SystemExit("No repo ingested — run the Phase 1 ingestion first.")

        # Stale edges from a previous ingest of this repo must go BEFORE the
        # symbols delete, or their FK references break re-ingest entirely.
        # Weakness.symbol_id also references symbols — NULL it out rather than
        # deleting, so ticketed findings survive a re-ingest.
        session.execute(delete(SymbolEdge).where(SymbolEdge.repo_id == repo.id))
        session.execute(
            update(Weakness)
            .where(Weakness.repo_id == repo.id, Weakness.symbol_id.is_not(None))
            .values(symbol_id=None)
        )
        session.execute(delete(Symbol).where(Symbol.repo_id == repo.id))

        files = session.scalars(
            select(File).where(File.repo_id == repo.id, File.category == "code",
                               File.content.is_not(None))
        ).all()
        files = [f for f in files if f.language in languages.SUPPORTED]

        rows: list[dict] = []
        for f in files:
            for sym in symbols.extract_symbols(f.content, f.language):
                rows.append(
                    {
                        "repo_id": repo.id,
                        "file_path": f.path,
                        "language": f.language,
                        "kind": sym.kind,
                        "name": sym.name,
                        "qualified_name": sym.qualified_name,
                        "start_line": sym.start_line,
                        "end_line": sym.end_line,
                        "signature": sym.signature,
                        "docstring": sym.docstring,
                        "code": sym.code,
                    }
                )
        if rows:
            session.execute(insert(Symbol), rows)
        total = len(rows)
        langs = sorted({f.language for f in files})
        print(f"[1/2] Extracted {total} symbols from {len(files)} file(s) [{', '.join(langs) or 'none'}.]")
    return total


def index_to_opensearch(embed: bool = True, repo_id: int | None = None) -> int:
    client = get_client()
    embedder = get_embedder() if embed else None
    dim = embedder.dim if embedder is not None else settings.embedding_dim
    with session_scope() as session:
        repo = _pick_repo(session, repo_id)
        if repo is None:
            raise SystemExit("No repo ingested — run the Phase 1 ingestion first.")
        # Only fully (re)creates the index on first use or a real dimension
        # change — then wipes just THIS repo's old docs, not the whole shared
        # index (that used to happen on every single ingest of any repo).
        code_index.create_index(client, dim=dim)
        code_index.delete_repo_docs(client, repo.id)
        q = select(Symbol).where(Symbol.repo_id == repo.id)
        syms = session.scalars(q).all()
        docs = [
            {
                "symbol_id": s.id,
                "repo_id": s.repo_id,
                "file_path": s.file_path,
                "language": s.language,
                "kind": s.kind,
                "name": s.name,
                "qualified_name": s.qualified_name,
                "signature": s.signature,
                "docstring": s.docstring,
                "code": s.code,
                "start_line": s.start_line,
                "end_line": s.end_line,
            }
            for s in syms
        ]

    if embedder is not None:
        # Embed real code units only; imports add cost and little semantic value.
        targets = [d for d in docs if d["kind"] != "import"]
        print(f"      embedding {len(targets)} symbols "
              f"({settings.embedding_provider}, dim={dim}) ...")
        vectors = embedder.embed_documents([_embedding_text(d) for d in targets])
        for doc, vec in zip(targets, vectors):
            doc["embedding"] = vec
        print("      embeddings attached.")
    else:
        print("      (no embedder -> BM25 only, no vectors)")

    n = code_index.index_documents(client, docs) if docs else 0
    print(f"[2/2] Indexed {n} symbols into OpenSearch index '{code_index.SYMBOL_INDEX}'.")
    return n


def _pick_repo(session, repo_id: int | None):
    """The requested repo, or the most recently ingested one."""
    if repo_id is not None:
        return session.get(Repo, repo_id)
    return session.scalar(select(Repo).order_by(Repo.id.desc()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract symbols + build BM25 index (Phase 2).")
    parser.add_argument("--query", help="After indexing, run a sample hybrid search")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--no-embeddings", action="store_true",
                        help="Skip Voyage embeddings (BM25-only index)")
    args = parser.parse_args()

    extract_to_postgres()
    index_to_opensearch(embed=not args.no_embeddings)

    if args.query:
        from archaeologist.retrieval.embeddings import get_embedder
        from archaeologist.retrieval.hybrid import hybrid_search

        client = get_client()
        embedder = get_embedder()
        with session_scope() as session:
            repo_id = _pick_repo(session, None).id
        mode = "hybrid (BM25 + vector, RRF)" if embedder else "BM25 only"
        print(f"\n=== {mode}: {args.query!r} ===")
        for hit in hybrid_search(client, embedder, args.query, k=args.k, repo_id=repo_id):
            loc = f"{hit['file_path']}:{hit['start_line']}"
            print(f"  {hit['score']:.4f}  [{hit['kind']:8}] {hit['qualified_name']:40.40} {loc}")


if __name__ == "__main__":
    main()
