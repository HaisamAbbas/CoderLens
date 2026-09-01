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


class User(Base):
    """A signed-in person, identified by their GitHub account (Phase 1 of the
    multi-user migration — see the plan for the full phase sequence). Nothing
    else in the schema references this yet; `Repo.user_id` (Phase 2) is what
    actually turns this into data isolation."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    github_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    github_login: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(300))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class Repo(Base):
    __tablename__ = "repos"
    # Was `url` unique=True — a single global repo table with no owner. Now
    # scoped per-user so two different users can each independently ingest
    # the same public URL (e.g. both exploring pallets/flask) without
    # colliding on one shared row.
    __table_args__ = (UniqueConstraint("user_id", "url", name="uq_repo_user_url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    url: Mapped[str] = mapped_column(String(500), index=True)
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


class IngestJob(Base):
    """Progress of one background ingest run. Persisted (not kept in an
    in-process dict) so a job survives an app restart — free-tier hosts like
    Render restart the process on every redeploy and on OOM, which used to
    orphan any in-flight job and leave the UI polling a 404 forever."""

    __tablename__ = "ingest_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # No repo_id — a Repo row doesn't exist yet when this job starts. user_id
    # is the only ownership anchor available until the ingest completes.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    repo_url: Mapped[str] = mapped_column(String(500), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)  # running | done | error
    step: Mapped[str] = mapped_column(String(50), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    stats: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class ConfluencePublishJob(Base):
    """Progress of one wiki publish to Confluence. Publishing ~6-8 sections
    means several sequential third-party calls each (title lookup, create or
    update, diagram render, attachment upload) — long enough to need a tracked,
    persisted background job (same reasoning as IngestJob) but with different
    fields, so it gets its own table rather than sharing ingest's."""

    __tablename__ = "confluence_publish_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)  # running|done|error
    section_keys: Mapped[list] = mapped_column(JSON)
    parent_url: Mapped[str | None] = mapped_column(String(500))
    results: Mapped[list | None] = mapped_column(JSON)  # appended live, per finished section
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class Weakness(Base):
    """One LLM-detected weakness (logic / security / style) in one file.
    The first place an LLM's structured output is persisted to a row rather
    than served transiently — hence the strict coercion before insert.

    Lifecycle: a scan REPLACES rows with status new|dismissed; status="ticketed"
    rows survive every re-scan (a human already acted on them). Snippets are
    NOT stored — they're sliced from File.content on read so they can't drift
    from the indexed source."""

    __tablename__ = "weaknesses"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), index=True)
    file_path: Mapped[str] = mapped_column(String(1000), index=True)
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    symbol_id: Mapped[int | None] = mapped_column(ForeignKey("symbols.id"), index=True)
    category: Mapped[str] = mapped_column(String(20), index=True)   # logic|security|style
    severity: Mapped[str] = mapped_column(String(10), index=True)   # high|medium|low
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    suggested_fix: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)  # new|dismissed|ticketed
    jira_url: Mapped[str | None] = mapped_column(String(500))
    head_sha: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class WeaknessScanJob(Base):
    """Progress of one weakness scan. Mirrors IngestJob's shape: one internal
    pipeline with discrete progress and no third-party writes, persisted so it
    survives process restarts on free-tier hosts."""

    __tablename__ = "weakness_scan_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)  # running|done|error
    files_scanned: Mapped[int] = mapped_column(Integer, default=0)
    files_total: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[list | None] = mapped_column(JSON)          # cap/truncation disclosures
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class JiraTicketJob(Base):
    """Progress of one batch of approved findings being turned into Jira issues.
    Mirrors ConfluencePublishJob's shape: independent external POSTs, each one
    separately failable, results appended live. On each success the matching
    Weakness row flips to status="ticketed" with its jira_url."""

    __tablename__ = "jira_ticket_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    finding_ids: Mapped[list] = mapped_column(JSON)
    results: Mapped[list | None] = mapped_column(JSON)        # {finding_id, status, url|error}, appended live
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class UserIntegration(Base):
    """One user's own Confluence/Jira credentials (Phase 4 of the multi-user
    migration) — these used to be global CONFLUENCE_*/JIRA_* env vars shared
    by everyone; now each user brings their own, so publishing/ticketing
    lands in THEIR space/project, not an operator-configured shared one.

    API tokens are stored encrypted (see security.py) — never in plaintext,
    never sent back to the frontend once saved."""

    __tablename__ = "user_integrations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)

    confluence_base_url: Mapped[str] = mapped_column(String(500), default="")
    confluence_email: Mapped[str] = mapped_column(String(300), default="")
    confluence_api_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    confluence_space_key: Mapped[str] = mapped_column(String(50), default="")

    jira_base_url: Mapped[str] = mapped_column(String(500), default="")
    jira_email: Mapped[str] = mapped_column(String(300), default="")
    jira_api_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    jira_project_key: Mapped[str] = mapped_column(String(50), default="")
    jira_issue_type: Mapped[str] = mapped_column(String(50), default="Task")

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
