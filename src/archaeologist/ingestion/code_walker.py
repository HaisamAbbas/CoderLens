"""Walk the working tree at HEAD into file rows (code + docs + config streams)."""

import hashlib
from pathlib import Path

from archaeologist.ingestion.classify import classify, detect_language

# Directories never worth indexing.
SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", "node_modules", ".venv", "venv", "dist", "build",
    ".tox", ".idea", ".vscode", "site-packages", ".eggs",
}
MAX_CONTENT_BYTES = 1_000_000  # store content only for files up to ~1MB


def _read_text(raw: bytes) -> str | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None  # binary
    # Normalize to LF. A raw byte decode (unlike text-mode file reads) does no
    # universal-newline translation, so CRLF/CR files would otherwise carry
    # literal \r into stored content and symbol spans — and browsers treat a
    # bare \r as its own forced line break in `white-space: pre`, doubling the
    # rendered row count and drifting the Reader's line-highlight further off
    # with every line beneath it.
    return text.replace("\r\n", "\n").replace("\r", "\n")


def walk_files(root: Path) -> list[dict]:
    """Return file rows (without repo_id) for every non-skipped file under root."""
    rows: list[dict] = []
    for path in _iter_paths(root):
        rel = path.relative_to(root).as_posix()
        size = path.stat().st_size
        raw = path.read_bytes() if size <= MAX_CONTENT_BYTES else b""
        content = _read_text(raw) if raw else None
        rows.append(
            {
                "path": rel,
                "language": detect_language(rel),
                "category": classify(rel),
                "size_bytes": size,
                "loc": content.count("\n") + 1 if content else 0,
                "content_sha": hashlib.sha1(raw).hexdigest() if raw else None,
                "content": content,
            }
        )
    return rows


def _iter_paths(root: Path):
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            yield Path(dirpath) / fname
