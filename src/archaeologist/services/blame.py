"""Live git-blame for a single file — real per-line author/commit attribution.

The already-ingested Commit/CommitFile tables only carry commit-file
granularity (which commits touched a file), not per-line attribution — real
blame needs git's own algorithm, so this shells out to the repo's actual
clone via GitPython rather than trying to reconstruct it from ingested rows.
"""

import git


def blame_file(cloned_path: str, path: str) -> list[dict]:
    """Per-line blame for `path` at HEAD of the repo cloned at `cloned_path`.

    Raises whatever GitPython raises (unknown path, not a git repo, ...) —
    callers turn that into a 404 rather than guessing at a fallback.
    """
    repo = git.Repo(cloned_path)
    blame = repo.blame("HEAD", path)
    lines: list[dict] = []
    line_no = 1
    for commit, hunk_lines in blame or []:
        for _ in hunk_lines:
            lines.append({
                "line": line_no,
                "sha": commit.hexsha[:8],
                "author": commit.author.name,
                "date": commit.committed_datetime.date().isoformat(),
                "message": commit.summary,
            })
            line_no += 1
    return lines
