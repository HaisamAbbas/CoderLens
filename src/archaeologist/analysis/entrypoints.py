"""Find where execution begins: web routes, CLI commands, background workers,
app factories, main() functions, and `if __name__ == '__main__'` scripts.

Detection is decorator/name/text based over the ingested symbols and files —
approximate but covers the common Python/Flask/Click/Celery patterns.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from archaeologist.models.entities import File, Symbol

ROUTE_PATH = re.compile(
    r"@\s*[\w.]+\.(?:route|get|post|put|delete|patch|websocket)\s*\(\s*[\"']([^\"']*)[\"']", re.I)
ROUTE_ANY = re.compile(r"@\s*[\w.]+\.(?:route|get|post|put|delete|patch|websocket)\s*\(", re.I)
ADD_URL = re.compile(r"\.add_url_rule\s*\(\s*[\"']([^\"']*)[\"']")
CLI_DEC = re.compile(r"@\s*(?:click\.(?:command|group)|[\w.]+\.command)\b")
WORKER_DEC = re.compile(r"@\s*(?:shared_task|celery\.task|[\w.]+\.task)\b")
MAIN_GUARD = re.compile(r"if\s+__name__\s*==\s*[\"']__main__[\"']")

# Ordering + display metadata for the UI.
KIND_ORDER = {"route": 0, "factory": 1, "cli": 2, "worker": 3, "main": 4, "module": 5}
KIND_LABEL = {
    "route": "HTTP route", "factory": "App factory", "cli": "CLI command",
    "worker": "Background task", "main": "main()", "module": "Script entry",
}


def _row(kind: str, label: str, sym: Symbol, detail: str = "") -> dict:
    return {"kind": kind, "label": label, "detail": detail, "path": sym.file_path,
            "line": sym.start_line, "symbol_id": sym.id}


def find_entrypoints(session: Session, repo_id: int) -> list[dict]:
    out: list[dict] = []

    symbols = session.scalars(
        select(Symbol).where(Symbol.repo_id == repo_id, Symbol.kind.in_(["function", "method"]))
    ).all()

    for s in symbols:
        if s.file_path.startswith("tests/"):
            continue  # test fixtures aren't real entrypoints
        code = s.code or ""
        route = ROUTE_PATH.search(code) or ADD_URL.search(code)
        if route:
            out.append(_row("route", route.group(1) or "/", s, s.qualified_name))
        elif ROUTE_ANY.search(code):
            out.append(_row("route", s.qualified_name, s))
        elif CLI_DEC.search(code):
            out.append(_row("cli", s.qualified_name, s))
        elif WORKER_DEC.search(code):
            out.append(_row("worker", s.qualified_name, s))
        elif s.name in ("create_app", "make_app"):
            out.append(_row("factory", s.qualified_name, s, "application factory"))
        elif s.name == "main" and s.kind == "function":
            out.append(_row("main", s.qualified_name, s))

    files = session.scalars(
        select(File).where(File.repo_id == repo_id,
                           File.category.in_(["code", "test", "config"]),
                           File.content.is_not(None))
    ).all()
    for f in files:
        if f.path.startswith("tests/"):
            continue
        c = f.content or ""
        m = MAIN_GUARD.search(c)
        if m:
            line = c[: m.start()].count("\n") + 1
            out.append({"kind": "module", "label": f.path.split("/")[-1],
                        "detail": "runs as a script", "path": f.path, "line": line, "symbol_id": None})

    out.sort(key=lambda e: (KIND_ORDER.get(e["kind"], 9), e["path"], e["line"]))
    return out
