"""Phase 7 eval CLI.

    uv run python -m archaeologist.eval.run                 # 40 commit-localization instances
    uv run python -m archaeologist.eval.run --limit 20 --show 8
"""

import argparse

from sqlalchemy import select

from archaeologist.eval import localization
from archaeologist.eval.dataset import build_localization_set
from archaeologist.models.db import session_scope
from archaeologist.models.entities import Repo


def main() -> None:
    parser = argparse.ArgumentParser(description="Localization eval (Phase 7).")
    parser.add_argument("--limit", type=int, default=40, help="Number of eval instances")
    parser.add_argument("--max-files", type=int, default=4, help="Max gold files per instance")
    parser.add_argument("--show", type=int, default=6, help="Per-instance examples to print")
    args = parser.parse_args()

    with session_scope() as session:
        repo = session.scalar(select(Repo))
        if repo is None:
            raise SystemExit("No repo ingested.")
        instances = build_localization_set(session, repo.id, limit=args.limit, max_files=args.max_files)

    print(f"Built {len(instances)} localization instances (commit subject -> changed src files).\n")
    rows = localization.evaluate(instances)
    agg = localization.aggregate(rows)

    print("=== Aggregate (retrieval-based localization) ===")
    for key, value in agg.items():
        print(f"  {key:12}: {value}")

    print(f"\n=== Sample ({min(args.show, len(rows))}) ===")
    for r in rows[: args.show]:
        mark = "✓" if r["hit@5"] else "✗"
        print(f"  {mark} [{r['id']}] recall@5={r['recall@5']:.2f} mrr={r['mrr']:.2f}  {r['question'][:52]}")
        print(f"      gold: {r['gold']}")
        print(f"      top:  {r['pred'][:5]}")


if __name__ == "__main__":
    main()
