"""Phase 1 ingestion CLI.

    uv run python -m archaeologist.ingestion.run                 # target from .env
    uv run python -m archaeologist.ingestion.run --skip-issues   # no GitHub calls
    uv run python -m archaeologist.ingestion.run --max-commits 1000 --max-issues 300
"""

import argparse

from archaeologist.config import settings
from archaeologist.ingestion.pipeline import ingest_repository


def _int_or_all(value: str) -> int | None:
    return None if value.lower() == "all" else int(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a repository into Postgres (Phase 1).")
    parser.add_argument("--repo", default=settings.target_repo_url, help="Repository URL")
    parser.add_argument("--max-commits", type=_int_or_all, default=3000,
                        help="Recent commits to ingest, or 'all' (default: 3000)")
    parser.add_argument("--max-issues", type=_int_or_all, default=500,
                        help="Issues/PRs to fetch, or 'all' (default: 500)")
    parser.add_argument("--skip-issues", action="store_true", help="Skip the GitHub issues stream")
    parser.add_argument("--only-issues", action="store_true",
                        help="Refresh only issues/PRs (repo must already be ingested)")
    args = parser.parse_args()

    stats = ingest_repository(
        repo_url=args.repo,
        max_commits=args.max_commits,
        max_issues=args.max_issues,
        skip_issues=args.skip_issues,
        only_issues=args.only_issues,
    )

    print("\n=== Ingest summary ===")
    print(f"repo         : {stats.repo_url}")
    print(f"files        : {stats.files}")
    print(f"commits      : {stats.commits}")
    print(f"commit_files : {stats.commit_files}")
    print(f"issues       : {stats.issues}")
    print(f"PRs          : {stats.prs}")
    for note in stats.notes:
        print(f"note         : {note}")


if __name__ == "__main__":
    main()
