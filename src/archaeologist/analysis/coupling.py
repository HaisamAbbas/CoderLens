"""Change coupling — files that tend to change together in the same commit.

A different signal from the (static) symbol graph: two files can be tightly
co-committed — a route and its test, a model and its migration — without
ever importing each other. Windowed to the most recent ~180 days relative to
the latest ingested commit (not wall-clock time, so this works on any
snapshot); falls back to the full history when that window is too sparse to
say anything. Commits touching an unusually large number of files (mass
reformats, merges) are excluded — they inflate every pair equally and add
no real signal.
"""

from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from archaeologist.models.entities import Commit, CommitFile

WINDOW_DAYS = 180
MAX_COMMIT_FILES = 20   # commits touching more files than this are noise, not signal
MIN_SUPPORT = 3         # a pair must co-change at least this many times to count

# CI workflows and lockfiles co-change constantly as pure bureaucracy (dependency
# bumps, CI config tweaks) — real every time, but not an architectural signal.
_NOISE_PREFIXES = (".github/",)
_NOISE_NAMES = {"uv.lock", "poetry.lock", "package-lock.json", "pyproject.toml",
                ".pre-commit-config.yaml"}


def _is_noise(path: str) -> bool:
    return path.startswith(_NOISE_PREFIXES) or path.rsplit("/", 1)[-1] in _NOISE_NAMES


def _files_by_commit(session: Session, repo_id: int, since) -> dict[str, list[str]]:
    q = select(CommitFile.commit_sha, CommitFile.path).where(CommitFile.repo_id == repo_id)
    if since is not None:
        q = (q.join(Commit, (Commit.repo_id == CommitFile.repo_id) & (Commit.sha == CommitFile.commit_sha))
              .where(Commit.authored_at >= since))
    by_commit: dict[str, list[str]] = defaultdict(list)
    for sha, path in session.execute(q):
        by_commit[sha].append(path)
    return by_commit


def find_change_coupling(session: Session, repo_id: int, limit: int = 12) -> dict:
    latest = session.scalar(select(func.max(Commit.authored_at)).where(Commit.repo_id == repo_id))
    since = latest - timedelta(days=WINDOW_DAYS) if latest else None

    by_commit = _files_by_commit(session, repo_id, since)
    windowed = True
    if sum(1 for v in by_commit.values() if len(set(v)) >= 2) < 5:
        by_commit = _files_by_commit(session, repo_id, since=None)
        windowed = False

    pair_count: Counter = Counter()
    file_count: Counter = Counter()
    for paths in by_commit.values():
        uniq = sorted(set(paths))
        if len(uniq) < 2 or len(uniq) > MAX_COMMIT_FILES:
            continue
        for p in uniq:
            file_count[p] += 1
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                pair_count[(uniq[i], uniq[j])] += 1

    rows = []
    for (a, b), n in pair_count.items():
        if n < MIN_SUPPORT or _is_noise(a) or _is_noise(b):
            continue
        # Support relative to how often either file changes alone — otherwise
        # the two globally busiest files always "win" regardless of any real link.
        denom = min(file_count[a], file_count[b])
        strength = round(n / denom, 2) if denom else 0.0
        rows.append({"a": a, "b": b, "co_changes": n, "strength": strength})

    rows.sort(key=lambda r: (-r["strength"], -r["co_changes"]))
    return {"pairs": rows[:limit], "windowed": windowed,
            "window_days": WINDOW_DAYS if windowed else None}
