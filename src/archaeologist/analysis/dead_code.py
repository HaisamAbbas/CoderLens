"""Dead-code candidates — functions/methods with no internal caller.

Framework-aware, same spirit as Axon's dead-code pass: entrypoints
(routes/CLI/workers/main), dunder methods, test functions, and decorated
definitions are never flagged, since something outside the visible call
graph invokes them (the framework, the test runner, decoration magic).

Important caveat for a *library* repo: a public method can have zero
internal callers simply because it's called by external user code, not by
the library itself — that isn't dead code. Rather than hide that noise, each
candidate is labeled `private` (leading-underscore name — genuinely internal
by convention) or `public` (exported API — usually a false positive here),
so the private ones are the real signal and are surfaced first.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from archaeologist.analysis.entrypoints import find_entrypoints
from archaeologist.models.entities import Symbol, SymbolEdge


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _is_decorated(code: str | None) -> bool:
    """`Symbol.code` spans the decorator too when one is present (see
    indexing/symbols.py) — a decorated function's real caller is often the
    decorator/framework machinery, not a name we can trace."""
    return bool(code) and code.lstrip().startswith("@")


def find_dead_code(session: Session, repo_id: int) -> dict:
    symbols = session.scalars(
        select(Symbol).where(Symbol.repo_id == repo_id, Symbol.kind.in_(("function", "method")))
    ).all()

    entry_ids = {e["symbol_id"] for e in find_entrypoints(session, repo_id) if e["symbol_id"]}
    called_ids = {
        row[0] for row in session.execute(
            select(SymbolEdge.dst_symbol_id)
            .where(SymbolEdge.repo_id == repo_id, SymbolEdge.dst_symbol_id.is_not(None))
            .distinct()
        )
    }

    candidates = []
    for s in symbols:
        if s.id in called_ids or s.id in entry_ids:
            continue
        if s.file_path.startswith(("tests/", "test/")) or s.name.startswith("test_"):
            continue
        if _is_dunder(s.name) or _is_decorated(s.code):
            continue
        private = s.name.startswith("_")
        candidates.append({
            "id": s.id, "qualified_name": s.qualified_name, "kind": s.kind,
            "path": s.file_path, "line": s.start_line,
            "signature": (s.signature or "").strip()[:200],
            "visibility": "private" if private else "public",
            "reason": ("No internal caller found." if private else
                       "No internal caller found — likely public API used by external code, "
                       "so this may be a false positive."),
        })

    candidates.sort(key=lambda c: (c["visibility"] != "private", c["path"], c["line"]))
    private_n = sum(1 for c in candidates if c["visibility"] == "private")
    return {
        "candidates": candidates,
        "counts": {"total": len(candidates), "private": private_n, "public": len(candidates) - private_n},
    }
