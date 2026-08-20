"""ORM entities for the ingested repository — the join keys that tie the
streams together are `path` (code/docs ↔ commit_files), `sha` (commits ↔
commit_files), and `number` (issues/PRs).

Phase 1 populates: Repo, File, Commit, CommitFile, Issue.
The dependency graph (symbol edges) arrives in Phase 2 with the AST.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from archaeologist.models.base import Base


class Repo(Base):
    __tablename__ = "repos"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    default_branch: Mapped[str | None] = mapped_column(String(200))
    head_sha: Mapped[str | None] = mapped_column(String(40))
    cloned_path: Mapped[str | None] = mapped_column(String(1000))
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Cached "Start here" wiki (mechanical + optional LLM-polished prose),
    # keyed to the head_sha it was computed for — a re-ingest naturally
    # invalidates it without any extra bookkeeping. Avoids re-running the LLM
    # curation call on every single page visit (deepwiki-open caches its
    # generated wiki the same way, keyed by repo instead of by commit).
    wiki_cache: Mapped[dict | None] = mapped_column(JSON)
    wiki_cache_sha: Mapped[str | None] = mapped_column(String(40))

    files: Mapped[list["File"]] = relationship(
        back_populates="repo", cascade="all, delete-orphan"
    )


class File(Base):
    """A file in the working tree at HEAD. `category` buckets the code vs docs
    vs config vs test streams; `content` is NULL for binary/oversized files."""

    __tablename__ = "files"
    __table_args__ = (UniqueConstraint("repo_id", "path", name="uq_file_repo_path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), index=True)
    path: Mapped[str] = mapped_column(String(1000), index=True)
    language: Mapped[str | None] = mapped_column(String(50))
    category: Mapped[str] = mapped_column(String(20), index=True)  # code|doc|config|test|other
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    loc: Mapped[int] = mapped_column(Integer, default=0)
    content_sha: Mapped[str | None] = mapped_column(String(40))
    content: Mapped[str | None] = mapped_column(Text)

    repo: Mapped[Repo] = relationship(back_populates="files")


class Commit(Base):
    __tablename__ = "commits"
    __table_args__ = (UniqueConstraint("repo_id", "sha", name="uq_commit_repo_sha"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), index=True)
    sha: Mapped[str] = mapped_column(String(40), index=True)
    author_name: Mapped[str | None] = mapped_column(String(300))
    author_email: Mapped[str | None] = mapped_column(String(300))
    authored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    committer_name: Mapped[str | None] = mapped_column(String(300))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message: Mapped[str | None] = mapped_column(Text)
    insertions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)
    files_changed: Mapped[int] = mapped_column(Integer, default=0)
    parents: Mapped[list | None] = mapped_column(JSON)  # list[str] of parent SHAs


class CommitFile(Base):
    """Per-file change within a commit — the diff-level join between the code
    and git-history streams. Links to Commit by `commit_sha` (not FK id) so
    bulk inserts stay simple."""

    __tablename__ = "commit_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), index=True)
    commit_sha: Mapped[str] = mapped_column(String(40), index=True)
    path: Mapped[str] = mapped_column(String(1000), index=True)
    change_type: Mapped[str | None] = mapped_column(String(2))  # A|M|D|R|T
    insertions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)


class Issue(Base):
    """A GitHub issue OR pull request (the REST issues API returns both;
    `is_pull_request` distinguishes them)."""

    __tablename__ = "issues"
    __table_args__ = (UniqueConstraint("repo_id", "number", name="uq_issue_repo_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), index=True)
    number: Mapped[int] = mapped_column(Integer, index=True)
    is_pull_request: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    title: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(String(20))
    author: Mapped[str | None] = mapped_column(String(300))
    labels: Mapped[list | None] = mapped_column(JSON)  # list[str]
    comments_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    url: Mapped[str | None] = mapped_column(String(500))


class Symbol(Base):
    """A code symbol extracted by the AST — the unit of code-aware chunking.
    `kind` is class|method|function|import; `qualified_name` is e.g. Flask.dispatch_request.
    Joins to the code stream by `file_path` and to git history via commit_files."""

    __tablename__ = "symbols"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), index=True)
    file_path: Mapped[str] = mapped_column(String(1000), index=True)
    language: Mapped[str] = mapped_column(String(50))
    kind: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    qualified_name: Mapped[str] = mapped_column(String(600), index=True)
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    signature: Mapped[str | None] = mapped_column(Text)
    docstring: Mapped[str | None] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(Text)


class SymbolEdge(Base):
    """A dependency edge between two symbols: `call` (src invokes dst),
    `inherit` (src subclasses dst). `dst_name` is the raw referenced name;
    `dst_symbol_id` is set only when it resolved to a symbol in this repo.

    Resolution is name-based (Python is dynamic), so edges are approximate:
    unresolvable calls (stdlib/builtins) and overly-ambiguous names are dropped."""

    __tablename__ = "symbol_edges"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), index=True)
    src_symbol_id: Mapped[int] = mapped_column(ForeignKey("symbols.id"), index=True)
    dst_symbol_id: Mapped[int | None] = mapped_column(ForeignKey("symbols.id"), index=True)
    dst_name: Mapped[str] = mapped_column(String(300), index=True)
    edge_type: Mapped[str] = mapped_column(String(12), index=True)  # call | inherit
    # 1.0 exact (unique name match) · 0.9 receiver known (self./ClassName. resolved
    # via MRO) · 0.5 fuzzy (name matches multiple unrelated symbols) · 0.6 ambiguous
    # inherit. See indexing/graph.py for how each tier is assigned.
    confidence: Mapped[float] = mapped_column(Float, default=1.0, index=True)


class Conversation(Base):
    """A saved Investigate or Codemap result — lets a user revisit a past
    question without re-running it. `result` stores the exact JSON payload the
    frontend originally received (answer/evidence/trace, or the codemap's
    nodes/edges/narrative), so reopening it is a plain read, no recomputation."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # investigate | codemap
    question: Mapped[str] = mapped_column(Text)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
