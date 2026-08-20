"""Build a localization eval set from the ingested commit history.

Each instance = (question = commit subject, gold = the source files that commit
changed). This mirrors SWE-bench's (issue → files-to-edit) structure without
needing an external dataset — a fair proxy for 'find the code behind a change'.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from archaeologist.models.entities import Commit, CommitFile


def _gold_code_files(paths: list[str]) -> list[str]:
    # Focus localization on non-test source files.
    return sorted({p for p in paths if p.endswith(".py") and not p.startswith("tests/")})


def build_localization_set(
    session: Session, repo_id: int, limit: int = 40, max_files: int = 4, min_subject: int = 12
) -> list[dict]:
    instances: list[dict] = []
    commits = session.scalars(
        select(Commit).where(Commit.repo_id == repo_id).order_by(Commit.authored_at.desc())
    ).all()

    for c in commits:
        if c.parents and len(c.parents) > 1:
            continue  # skip merges
        subject = (c.message or "").splitlines()[0].strip() if c.message else ""
        if len(subject) < min_subject:
            continue
        paths = session.scalars(
            select(CommitFile.path).where(
                CommitFile.repo_id == repo_id, CommitFile.commit_sha == c.sha
            )
        ).all()
        gold = _gold_code_files(paths)
        if not (1 <= len(gold) <= max_files):
            continue
        instances.append({"id": c.sha[:8], "question": subject, "gold_files": gold})
        if len(instances) >= limit:
            break
    return instances
