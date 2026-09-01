"""Ask the archaeologist a question from the CLI.

    uv run python -m archaeologist.rag.ask "why does Flask use an application context"
    uv run python -m archaeologist.rag.ask "what changed about async views" --streams commit issue
"""

import argparse

from sqlalchemy import select

from archaeologist.models.db import session_scope
from archaeologist.models.entities import Repo
from archaeologist.rag.pipeline import answer_question


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask CoderLens a question about the codebase (Phase 5 RAG).")
    parser.add_argument("question")
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--repo-id", type=int, default=None,
                        help="Repo to search (default: most recently ingested)")
    parser.add_argument("--streams", nargs="*", default=None,
                        help="Limit to streams: code doc commit issue")
    args = parser.parse_args()

    with session_scope() as session:
        repo = (session.get(Repo, args.repo_id) if args.repo_id is not None
                else session.scalar(select(Repo).order_by(Repo.id.desc())))
        if repo is None:
            raise SystemExit("No repo ingested — run ingestion first.")
        repo_id = repo.id

    result = answer_question(args.question, repo_id, k=args.k, streams=args.streams)

    print(f"Q: {result.question}\n")
    print("EVIDENCE:")
    for i, e in enumerate(result.evidence, 1):
        print(f"  [{i}] ({e['stream']:6}) {e['citation']:24.24} {e['title'][:50]}")
    print("\nANSWER:\n")
    print(result.answer)


if __name__ == "__main__":
    main()
