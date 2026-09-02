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
# Aggregate caps on a single ingest — MAX_CONTENT_BYTES only bounds one file;
# without these a repo with many large-ish files can still exhaust worker
# memory (walk_files holds every row, content included, in one list) or fill
# the ephemeral disk. Generous enough for any normal repository.
MAX_FILES = 20_000
MAX_TOTAL_BYTES = 500_000_000


class RepoTooLargeError(RuntimeError):
    pass


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
    """Return file rows (without repo_id) for every non-skipped file under root.

    Raises RepoTooLargeError if the repository exceeds MAX_FILES or
    MAX_TOTAL_BYTES — surfaced to the ingest job as a clean failure rather
    than an OOM or a filled disk partway through."""
    root = root.resolve()
    rows: list[dict] = []
    total_bytes = 0
    for path in _iter_paths(root):
        if len(rows) >= MAX_FILES:
            raise RepoTooLargeError(
                f"repository has more than {MAX_FILES:,} files — too large to ingest")
        rel = path.relative_to(root).as_posix()
        size = path.stat().st_size
        total_bytes += size
        if total_bytes > MAX_TOTAL_BYTES:
            raise RepoTooLargeError(
                f"repository exceeds {MAX_TOTAL_BYTES:,} bytes — too large to ingest")
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

    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not (Path(dirpath) / d).is_symlink()
        ]
        for fname in filenames:
            path = Path(dirpath) / fname
            # A symlink inside a cloned repo can point anywhere on this host
            # (/etc/passwd, /proc/self/environ, ...) — read_bytes() would
            # dereference it and store the target's content as if it were
            # part of the repo. Never follow one, and only ever read a
            # plain regular file (skips FIFOs/devices too, which would
            # otherwise block a read indefinitely).
            if path.is_symlink() or not path.is_file():
                continue
            if not path.resolve().is_relative_to(root):
                continue
            yield path
