"""Extract the git-history stream: commits + per-file changes (commit_files)."""

import git

# GitPython's NULL_TREE sentinel lets us diff the initial commit against nothing.
from git import NULL_TREE


def walk_commits(repo: git.Repo, max_commits: int | None = None) -> tuple[list[dict], list[dict]]:
    """Return (commit_rows, commit_file_rows), newest first.

    max_commits caps how many recent commits to ingest (None = all).
    """
    commit_rows: list[dict] = []
    file_rows: list[dict] = []

    for i, commit in enumerate(repo.iter_commits()):
        if max_commits is not None and i >= max_commits:
            break

        parents = [p.hexsha for p in commit.parents]
        total = commit.stats.total
        commit_rows.append(
            {
                "sha": commit.hexsha,
                "author_name": commit.author.name,
                "author_email": commit.author.email,
                "authored_at": commit.authored_datetime,
                "committer_name": commit.committer.name,
                "committed_at": commit.committed_datetime,
                "message": commit.message,
                "insertions": total.get("insertions", 0),
                "deletions": total.get("deletions", 0),
                "files_changed": total.get("files", 0),
                "parents": parents,
            }
        )

        # change types (A/M/D/R/T) from the diff against the first parent.
        base = commit.parents[0] if commit.parents else NULL_TREE
        try:
            change_by_path = {
                (d.b_path or d.a_path): d.change_type for d in commit.diff(base)
            }
        except Exception:
            change_by_path = {}

        for path, stat in commit.stats.files.items():
            file_rows.append(
                {
                    "commit_sha": commit.hexsha,
                    "path": path,
                    "change_type": change_by_path.get(path, "M"),
                    "insertions": stat.get("insertions", 0),
                    "deletions": stat.get("deletions", 0),
                }
            )

    return commit_rows, file_rows
