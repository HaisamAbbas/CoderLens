"""Generate the dependency-graph HTML.

    uv run python -m archaeologist.viz.run
    uv run python -m archaeologist.viz.run --include-tests --out data/graph.html
"""

import argparse
from pathlib import Path

from sqlalchemy import select

from archaeologist.models.db import session_scope
from archaeologist.models.entities import Repo
from archaeologist.viz.export import export_combined, export_file_graph, export_symbol_graph
from archaeologist.viz.render import render, render_linked


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a dependency graph (Phase 8 viz).")
    parser.add_argument("--out", default="data/dependency_graph.html")
    parser.add_argument("--include-tests", action="store_true")
    parser.add_argument("--min-weight", type=int, default=1)
    parser.add_argument("--symbols", metavar="PATH_PREFIX", nargs="?", const="",
                        help="Symbol-level graph, optionally scoped to a path prefix")
    parser.add_argument("--linked", action="store_true",
                        help="File atlas with click-to-drill into each file's symbols")
    args = parser.parse_args()

    with session_scope() as session:
        repo = session.scalar(select(Repo))
        if repo is None:
            raise SystemExit("No repo ingested.")
        name = repo.name.capitalize()
        if args.symbols is not None:
            data = export_symbol_graph(session, repo.id, path_prefix=args.symbols or None)
            html = render(data, title=f"{name} — Symbol Graph")
        elif args.linked:
            data = export_combined(session, repo.id, exclude_tests=not args.include_tests)
            html = render_linked(data, title=f"{name} Dependency Atlas")
            data = data["files"]
        else:
            data = export_file_graph(session, repo.id,
                                     exclude_tests=not args.include_tests, min_weight=args.min_weight)
            html = render(data, title=f"{name} Dependency Atlas")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out}  ({len(data['nodes'])} nodes, {len(data['links'])} links)")


if __name__ == "__main__":
    main()
