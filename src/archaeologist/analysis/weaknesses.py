"""LLM weakness scan — the first analysis in this codebase whose output is
PERSISTED (the `weaknesses` table) rather than served transiently.

One LLM call per source file (per-symbol would explode call counts on a real
repo; per-repo loses line precision), capped at 50 files prioritized by commit
churn — the same cap philosophy as pipeline.py's no-token issue ceiling, since
an LLM call costs far more per unit than a REST page. Every cap and truncation
is DISCLOSED via returned notes (pipeline.py's stats.notes ethos): nothing is
silently partial.

Findings are self-assessed by the model (category logic|security|style,
severity high|medium|low) and coerced strictly before persistence — wrong
categories drop, miscalibrated severities coerce to medium, line numbers clamp
into the file. Like dead_code.py's public-API candidates, detection quality is
inherently fuzzy; review-before-acting is the product, not a bug.
"""

from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from archaeologist.models.entities import CommitFile, File, Weakness
from archaeologist.rag.llm import call_llm, llm_available, parse_llm_json

MAX_FILES = 50          # per-run ceiling unless scan_all=true lifts it
MAX_FILE_CHARS = 8000   # per-file content budget sent to the model

WEAKNESS_SYS = """You are a senior code reviewer scanning one source file for weaknesses.
Look for real, concrete issues in three categories:
- "logic": bugs, broken edge cases, wrong conditions, resource leaks, race conditions
- "security": injection, unsafe deserialization, secrets handling, authz gaps, unsafe defaults
- "style": maintainability hazards that matter (dead branches, dangerous patterns, misleading names)

Rules:
- Only report what the shown code actually supports — cite exact line numbers from it.
- No style nits about formatting/quotes; style findings must affect correctness or maintainability.
- Return NOTHING but JSON: {"findings": [{"title", "description", "category",
  "severity", "start_line", "end_line", "suggested_fix"}]}
- "findings" may be [] when the file is clean."""

_CATEGORIES = {"logic", "security", "style"}
_SEVERITIES = {"high", "medium", "low"}

# Compact extension map — only what a code file is likely to have (wiki.py's
# _EXT_LANG covers more, but importing wiki here drags its whole graph stack).
_LANGS = {
    "py": "python", "pyi": "python", "js": "javascript", "jsx": "javascript",
    "ts": "typescript", "tsx": "typescript", "go": "go", "rs": "rust",
    "java": "java", "rb": "ruby", "php": "php", "cs": "csharp", "c": "c",
    "cpp": "cpp", "sh": "bash", "sql": "sql", "yaml": "yaml", "yml": "yaml",
}


def _lang_of(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _LANGS.get(ext, "")


def _is_test_path(path: str) -> bool:
    return path.startswith(("tests/", "test/")) or path.startswith("test_")


def select_files(session: Session, repo_id: int, limit: int,
                 scan_all: bool) -> tuple[list[File], int]:
    """Code files ordered by commit churn desc then LOC desc (hot files hide the
    most bugs). Returns (selected, total_code_files) so callers can disclose caps.
    Public because the job service selects in its own short DB scope, then fans
    out with NO session held — an LLM sweep runs minutes; a pinned Postgres
    transaction that whole time is exactly what session_scope-per-step avoids."""
    churn: dict[str, int] = defaultdict(int)
    for path, n in session.execute(
        select(CommitFile.path, func.count())
        .where(CommitFile.repo_id == repo_id)
        .group_by(CommitFile.path)
    ):
        churn[path] = n
    files = session.scalars(
        select(File).where(File.repo_id == repo_id, File.category == "code")
    ).all()
    candidates = [f for f in files if not _is_test_path(f.path)]
    candidates.sort(key=lambda f: (-churn.get(f.path, 0), -f.loc, f.path))
    total = len(candidates)
    return (candidates if scan_all else candidates[:limit]), total


def _coerce_finding(raw: dict, file_path: str, max_line: int) -> dict | None:
    """Strict pre-persistence validation — LLM output becomes a DB row here.
    Wrong/missing category drops (it's the UI partition key); bad severity
    coerces rather than drops (miscalibration isn't a false finding); lines
    clamp into the actual file."""
    if not isinstance(raw, dict):
        return None
    category = str(raw.get("category", "")).strip().lower()
    if category not in _CATEGORIES:
        return None
    title = str(raw.get("title", "")).strip()[:300]
    description = str(raw.get("description", "")).strip()[:4000]
    if not title or not description:
        return None
    severity = str(raw.get("severity", "")).strip().lower()
    if severity not in _SEVERITIES:
        severity = "medium"
    start = raw.get("start_line")
    if not isinstance(start, int) or isinstance(start, bool) or start < 1:
        return None
    start = min(start, max_line)
    end = raw.get("end_line")
    bad_end = not isinstance(end, int) or isinstance(end, bool) or end < start
    end = start if bad_end else min(end, max_line)
    fix = raw.get("suggested_fix")
    return {"file_path": file_path, "category": category, "severity": severity,
            "title": title, "description": description,
            "start_line": start, "end_line": end,
            "suggested_fix": str(fix).strip()[:2000] if fix else None}


def _scan_file(file: File) -> list[dict]:
    if not llm_available() or not file.content:
        return []
    body = file.content[:MAX_FILE_CHARS]
    suffix = "\n    # … (truncated)" if len(file.content) > MAX_FILE_CHARS else ""
    user = f"File: {file.path}\n\n```{_lang_of(file.path)}\n{body}{suffix}\n```"
    try:
        raw = call_llm(WEAKNESS_SYS, user, max_tokens=1500, temperature=0.1,
                       label="weakness-scan")
        data = parse_llm_json(raw)
    except Exception:  # noqa: BLE001 - one file's failure never sinks the scan
        return []
    items = data.get("findings")
    if not isinstance(items, list):
        return []
    max_line = file.content.count("\n") + 1
    out = []
    for item in items:
        finding = _coerce_finding(item, file.path, max_line)
        if finding is not None:
            out.append(finding)
    return out


def _cap_notes(files: list[File], total_code: int, scan_all: bool) -> list[str]:
    """Disclose every cap/truncation — nothing is silently partial."""
    notes: list[str] = []
    if not scan_all and total_code > len(files):
        notes.append(
            f"Capped to {len(files)} of {total_code} code files "
            "(prioritized by commit churn, then LOC) — re-scan with scan_all=true "
            "for full coverage."
        )
    truncated = sum(1 for f in files if f.content and len(f.content) > MAX_FILE_CHARS)
    if truncated:
        notes.append(
            f"{truncated} file(s) exceeded the {MAX_FILE_CHARS}-char per-file budget "
            "and were truncated before scanning."
        )
    if not llm_available():
        notes.append("No LLM provider available — scan ran in offline mode and found nothing.")
    return notes


def scan_files(files: list[File], total_code: int, scan_all: bool,
               on_progress: Callable[[int, int], None] | None = None,
              ) -> tuple[list[dict], list[str]]:
    """Fan the selected files out concurrently (same shape as wiki.py's
    per-section prose fan-out). No DB session involved — safe to run for
    minutes. Returns (findings sorted for stable display, notes)."""
    notes = _cap_notes(files, total_code, scan_all)
    findings: list[dict] = []
    done = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_scan_file, f) for f in files]
        for future in as_completed(futures):
            try:
                findings.extend(future.result())
            except Exception:  # noqa: BLE001 - defensive; _scan_file already guards
                pass
            done += 1
            if on_progress is not None:
                on_progress(done, len(files))

    findings.sort(key=lambda f: (f["file_path"], f["start_line"], f["title"]))
    return findings, notes


def scan_repo(session: Session, repo_id: int, limit: int = MAX_FILES,
              scan_all: bool = False,
              on_progress: Callable[[int, int], None] | None = None,
             ) -> tuple[list[dict], list[str]]:
    """Convenience wrapper (selection + fan-out in one call). The job service
    uses select_files/scan_files separately to keep DB scopes short."""
    files, total_code = select_files(session, repo_id, limit, scan_all)
    return scan_files(files, total_code, scan_all, on_progress)


def replace_findings(session: Session, repo_id: int, head_sha: str | None,
                     rows: list[dict]) -> None:
    """Replace new/dismissed rows with the fresh scan; ticketed rows survive —
    a human already acted on them, and without diffing there's no safe way to
    reattach history to a re-detected finding."""
    session.execute(delete(Weakness).where(
        Weakness.repo_id == repo_id, Weakness.status.in_(("new", "dismissed"))))
    for row in rows:
        row["repo_id"] = repo_id
        row["head_sha"] = head_sha
    if rows:
        session.execute(insert(Weakness), rows)
