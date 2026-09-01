"""Build unified 'evidence' documents from the non-code streams
(docs, commits, issues) so retrieval can span all of them.

Each evidence doc has a common shape: stream, ref_id, title, text, citation,
plus stream-specific metadata. Code lives in its own `code_symbols` index;
`retrieval.multi` fuses across both.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from archaeologist.models.entities import Commit, File, Issue

DOC_CHUNK_SIZE = 1500


def chunk_text(text: str, size: int = DOC_CHUNK_SIZE) -> list[str]:
    """Split on blank lines, accreting paragraphs up to ~size chars."""
    paras = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current = ""
    for para in paras:
        if current and len(current) + len(para) > size:
            chunks.append(current.strip())
            current = ""
        current += para + "\n\n"
    if current.strip():
        chunks.append(current.strip())
    return chunks


def build_evidence_docs(session: Session, repo_id: int) -> list[dict]:
    docs: list[dict] = []
    docs += _doc_files(session, repo_id)
    docs += _commits(session, repo_id)
    docs += _issues(session, repo_id)
    return docs


def _doc_files(session: Session, repo_id: int) -> list[dict]:
    out: list[dict] = []
    files = session.scalars(
        select(File).where(File.repo_id == repo_id, File.category == "doc",
                           File.content.is_not(None))
    ).all()
    for f in files:
        for i, chunk in enumerate(chunk_text(f.content)):
            out.append({
                "repo_id": repo_id,
                "stream": "doc",
                "ref_id": f"{f.path}#{i}",
                "title": f.path,
                "text": chunk,
                "citation": f.path if i == 0 else f"{f.path} (part {i + 1})",
                "file_path": f.path,
            })
    return out


def _commits(session: Session, repo_id: int) -> list[dict]:
    out: list[dict] = []
    for c in session.scalars(select(Commit).where(Commit.repo_id == repo_id)).all():
        subject = (c.message or "").splitlines()[0] if c.message else ""
        out.append({
            "repo_id": repo_id,
            "stream": "commit",
            "ref_id": c.sha,
            "title": subject,
            "text": c.message or "",
            "citation": f"commit {c.sha[:8]}",
            "sha": c.sha,
            "author": c.author_name,
            "date": c.authored_at.isoformat() if c.authored_at else None,
        })
    return out


def _issues(session: Session, repo_id: int) -> list[dict]:
    out: list[dict] = []
    for it in session.scalars(select(Issue).where(Issue.repo_id == repo_id)).all():
        kind = "PR" if it.is_pull_request else "issue"
        out.append({
            "repo_id": repo_id,
            "stream": "issue",
            "ref_id": str(it.number),
            "title": it.title or "",
            "text": f"{it.title or ''}\n\n{it.body or ''}",
            "citation": f"{kind} #{it.number}",
            "number": it.number,
            "state": it.state,
            "kind": kind,
            "url": it.url,
        })
    return out
