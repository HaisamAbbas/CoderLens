"""JSON API for the web frontend. Thin wrappers over the engine.

Everything the React app needs: repo summary, orientation (overview), the file
tree, file source + symbols, symbol detail (callers/callees), the dependency
graph, cross-stream search, repo management (add / refresh / job status),
and the ask/investigate endpoints (including streaming investigate).

Every route below that touches repo data requires a signed-in user
(`user: User = CurrentUser`) and resolves data scoped to THAT user's repos —
Phase 2 of the multi-user migration. `/api/status` is the one exception
(server-wide provider config, not user data) and stays open, matching
`/health`.
"""

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from archaeologist.agent.graph import investigate_stream
from archaeologist.auth import CurrentUser, RequireRealUser
from archaeologist.config import settings
from archaeologist.rate_limit import limiter
from archaeologist.indexing.opensearch_client import get_client
from archaeologist.ingestion.repository import normalize_repo_url
from archaeologist.models.db import session_scope
from archaeologist.models.entities import (
    Commit,
    CommitFile,
    File,
    Issue,
    Repo,
    Symbol,
    SymbolEdge,
    User,
    Weakness,
)
from archaeologist.rag.llm import llm_available
from archaeologist.retrieval.embeddings import get_embedder
from archaeologist.retrieval.multi import search_all
from archaeologist.services import ingest
from archaeologist.services.blame import blame_file
from archaeologist.services.conversations import (
    delete_conversation,
    get_conversation,
    list_conversations,
    save_conversation,
)
from archaeologist.viz.export import export_file_graph, export_symbol_graph

router = APIRouter(prefix="/api", tags=["api"])
_logger = logging.getLogger("archaeologist")


def _active_order():
    """Ordering that puts the active repo first: most recently ingested.

    Not by id. Re-ingesting an existing repo updates its row in place rather
    than inserting a new one, so a repo added long ago and refreshed a minute
    ago keeps its low id — ordering by id then silently hands every page a
    different repo than the one just ingested. `ingested_at` is null while a
    first ingest is still running, so nulls sort last and id breaks ties.
    """
    return (Repo.ingested_at.desc().nullslast(), Repo.id.desc())


def _repo(session, user: User) -> Repo:
    """The active repo — the CURRENT USER's most recently ingested one.
    Never resolves another user's repo, even if theirs is more recent."""
    repo = session.scalar(
        select(Repo).where(Repo.user_id == user.id).order_by(*_active_order())
    )
    if repo is None:
        raise HTTPException(404, "No repository ingested yet.")
    return repo


def _owns_repo(session, user: User, repo_id: int) -> bool:
    """Ownership check for routes that take a raw id (symbol_id, job_id's
    repo_id, ...) instead of resolving through _repo() — these previously had
    NO ownership check at all (a real IDOR: any signed-in user could fetch
    any other user's data by guessing/incrementing an id)."""
    return session.scalar(
        select(Repo.id).where(Repo.id == repo_id, Repo.user_id == user.id)
    ) is not None


def _effective_token(session, user: User, explicit_token: str) -> str:
    """An explicitly-supplied token always wins (lets a signed-in user
    override their saved PAT for one specific repo); otherwise falls back to
    the user's own saved GitHub PAT, if they have one and aren't a guest —
    guests can't save one at all (RequireRealUser-gated), so there's nothing
    to fall back to for them."""
    if explicit_token or user.is_guest:
        return explicit_token
    from archaeologist.services import user_integrations

    integ = user_integrations.get(session, user.id)
    if user_integrations.github_pat_configured(integ):
        return user_integrations.github_pat(integ)
    return ""


def _count(session, model, repo_id) -> int:
    return session.scalar(
        select(func.count()).select_from(model).where(model.repo_id == repo_id)
    ) or 0


@router.get("/status")
def status(user: User = CurrentUser) -> dict:
    """Runtime capabilities — which LLM/embedding providers are active. Lets the
    UI show that the app works without any API key (local Ollama / offline).
    The LLM/embedding info is server-wide config, not user data. `user` is
    never None now — a logged-out visitor resolves to a guest account (see
    auth.py) — so confluence/jira "configured" naturally reads false for a
    guest (they can't have UserIntegration credentials; that requires
    RequireRealUser) without any special-casing here."""
    from archaeologist.rag.llm import active_model, llm_available, resolve_provider
    from archaeologist.retrieval.embeddings import get_embedder
    from archaeologist.services import user_integrations

    embedder = None
    try:
        embedder = get_embedder()
    except Exception:  # noqa: BLE001 - embeddings are optional (BM25-only)
        pass
    embedding_provider = settings.embedding_provider

    with session_scope() as s:
        integ = user_integrations.get(s, user.id)
        confluence_ok = user_integrations.confluence_configured(integ)
        jira_ok = user_integrations.jira_configured(integ)

    return {
        "llm": {
            "provider": resolve_provider(),
            "model": active_model(),
            "available": llm_available(),
        },
        "embedding": {
            "provider": embedding_provider,
            "model": (settings.local_embedding_model if embedding_provider == "local"
                      else settings.voyage_model if embedder is not None else None),
            "active": embedder is not None,
        },
        # Whether the wiki-publish feature should be offered at all — the UI
        # hides the Confluence entry point unless THIS user has connected one.
        "confluence": {"configured": confluence_ok},
        # Same gating for the weaknesses → Jira flow.
        "jira": {"configured": jira_ok},
    }


@router.get("/repo")
def repo_summary(user: User = CurrentUser) -> dict:
    with session_scope() as s:
        r = _repo(s, user)
        return {
            "name": r.name,
            "url": r.url,
            "default_branch": r.default_branch,
            "head_sha": r.head_sha,
            "ingested_at": r.ingested_at.isoformat() if r.ingested_at else None,
            "counts": {
                "files": _count(s, File, r.id),
                "symbols": _count(s, Symbol, r.id),
                "commits": _count(s, Commit, r.id),
                "issues": _count(s, Issue, r.id),
                "edges": _count(s, SymbolEdge, r.id),
            },
        }


def _repo_row(s, r: Repo) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "url": r.url,
        "default_branch": r.default_branch,
        "head_sha": r.head_sha,
        "ingested_at": r.ingested_at.isoformat() if r.ingested_at else None,
        "active": True,
        "counts": {
            "files": _count(s, File, r.id),
            "symbols": _count(s, Symbol, r.id),
            "commits": _count(s, Commit, r.id),
            "issues": _count(s, Issue, r.id),
            "edges": _count(s, SymbolEdge, r.id),
        },
    }


@router.get("/repos")
def repos(user: User = CurrentUser) -> dict:
    """Every repo THIS USER has ingested, newest first. The first entry is
    the active one — same ordering as _repo(), so the list and the active
    repo never disagree."""
    with session_scope() as s:
        rows = s.scalars(
            select(Repo).where(Repo.user_id == user.id).order_by(*_active_order())
        ).all()
        return {"repos": [_repo_row(s, r) for r in rows]}


class AddRepoBody(BaseModel):
    url: str
    token: str = ""   # optional GitHub PAT for private repos — used transiently at clone, never stored


@router.post("/repos")
@limiter.limit("10/minute")
def add_repo(request: Request, body: AddRepoBody, user: User = CurrentUser) -> dict:
    """Start a full ingest (clone → streams → symbols → indexes → graph) of a
    repo URL in the background, owned by the signed-in user. Returns the job;
    the UI polls its status.

    Idempotent per-user: if THIS user already has an ingest for this URL
    running, the existing job is returned instead of starting a second one —
    a different user's in-flight job for the same URL is unaffected.
    """
    explicit_token = body.token.strip()
    if explicit_token and user.is_guest:
        raise HTTPException(401, "Sign in with GitHub to ingest a private repository.")
    url = body.url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(422, "Enter a full repository URL, e.g. https://github.com/owner/repo")
    # A URL copied straight from a browser while viewing a branch/file (e.g.
    # ".../owner/repo/tree/main") means the repo, not a git remote — normalize
    # before it's stored/used anywhere downstream, not just at clone time.
    url = normalize_repo_url(url)
    with session_scope() as s:
        token = _effective_token(s, user, explicit_token)
    job = ingest.start_ingest(url, user.id, token=token)
    return {"job_id": job["id"], "repo_url": url, "status": job["status"]}  # token never echoed


@router.post("/repos/{repo_id}/activate")
def activate_repo(repo_id: int, user: User = CurrentUser) -> dict:
    """Make an already-ingested repo the active one again, without
    re-ingesting — `_repo()` always resolves to the most-recently-ingested
    row, so bumping `ingested_at` to now is enough to switch. This is the
    whole mechanism behind the frontend's repo switcher: it doesn't need
    explicit repo_id plumbing through every page, just a way to change which
    repo "most recent" currently means for this user."""
    with session_scope() as s:
        repo = s.get(Repo, repo_id)
        if repo is None or repo.user_id != user.id:
            raise HTTPException(404, "Repo not found")
        repo.ingested_at = datetime.now(timezone.utc)
        return {"id": repo.id, "name": repo.name}


@router.get("/repos/jobs/{job_id}")
def repo_job(job_id: str, user: User = CurrentUser) -> dict:
    status = ingest.job_status(job_id)
    if status is None or status["user_id"] != user.id:
        raise HTTPException(404, f"Unknown ingest job: {job_id}")
    return status


class RefreshRepoBody(BaseModel):
    token: str = ""   # manual recovery path: re-supply after an ephemeral-disk wipe killed the local clone


@router.post("/repos/refresh")
@limiter.limit("10/minute")
def refresh_repo(
    request: Request, body: RefreshRepoBody | None = None, user: User = CurrentUser,
) -> dict:
    """Re-ingest the active repo end-to-end (picks up new commits / files).

    Today refresh never contacts the remote unless the local clone is gone
    (ephemeral disk) — in that edge case a private repo needs its PAT again;
    pass it here. No dedicated frontend flow; this is a fallback."""
    explicit_token = (body.token if body else "").strip()
    if explicit_token and user.is_guest:
        raise HTTPException(401, "Sign in with GitHub to use a private-repo access token.")
    with session_scope() as s:
        r = _repo(s, user)
        url = r.url
        token = _effective_token(s, user, explicit_token)
    job = ingest.start_ingest(url, user.id, token=token)
    return {"job_id": job["id"], "repo_url": url, "status": job["status"]}


@router.get("/overview")
def overview(user: User = CurrentUser) -> dict:
    with session_scope() as s:
        r = _repo(s, user)
        # degree per file (from symbol edges aggregated to files)
        sym = {x.id: x for x in s.scalars(select(Symbol).where(Symbol.repo_id == r.id))}
        deg: dict[str, int] = defaultdict(int)
        for e in s.scalars(select(SymbolEdge).where(
                SymbolEdge.repo_id == r.id, SymbolEdge.dst_symbol_id.is_not(None))):
            a, b = sym.get(e.src_symbol_id), sym.get(e.dst_symbol_id)
            if a and b and a.file_path != b.file_path:
                deg[a.file_path] += 1
                deg[b.file_path] += 1
        # churn per file (commit_files)
        churn: dict[str, int] = defaultdict(int)
        for path, n in s.execute(
            select(CommitFile.path, func.count()).where(CommitFile.repo_id == r.id)
            .group_by(CommitFile.path)
        ):
            churn[path] = n

        def is_core(p: str) -> bool:
            return p.startswith("src/") and not p.startswith("tests/")

        core = sorted((p for p in deg if is_core(p)), key=lambda p: -deg[p])
        reading = [{"path": p, "degree": deg[p],
                    "reason": f"{deg[p]} internal dependencies — central to how the system fits together."}
                   for p in core[:6]]

        # hotspots: normalized churn × degree
        cand = [(p, churn.get(p, 0) * (deg.get(p, 0) + 1)) for p in set(deg) | set(churn) if is_core(p)]
        cand.sort(key=lambda kv: -kv[1])
        top = cand[:5]
        mx = top[0][1] if top and top[0][1] else 1
        hotspots = [{"path": p, "score": round(v / mx, 2), "churn": churn.get(p, 0),
                     "coupling": deg.get(p, 0)} for p, v in top]

        counts = {
            "files": _count(s, File, r.id), "symbols": _count(s, Symbol, r.id),
            "commits": _count(s, Commit, r.id), "issues": _count(s, Issue, r.id),
            "edges": _count(s, SymbolEdge, r.id),
        }
        most_central = core[0].rsplit("/", 1)[-1] if core else "—"
        return {
            "name": r.name, "url": r.url, "counts": counts,
            "most_central": most_central, "reading_path": reading, "hotspots": hotspots,
        }


@router.get("/tree")
def tree(user: User = CurrentUser) -> dict:
    with session_scope() as s:
        r = _repo(s, user)
        rows = s.execute(
            select(File.path, File.category, File.language, File.loc)
            .where(File.repo_id == r.id).order_by(File.path)
        )
        files = [{"path": p, "category": c, "language": lang, "loc": loc}
                 for p, c, lang, loc in rows]
        sym_counts: dict[str, int] = defaultdict(int)
        for path, n in s.execute(
            select(Symbol.file_path, func.count()).where(
                Symbol.repo_id == r.id, Symbol.kind != "import").group_by(Symbol.file_path)
        ):
            sym_counts[path] = n
        for f in files:
            f["symbols"] = sym_counts.get(f["path"], 0)
        return {"files": files}


@router.get("/file")
def file_content(path: str = Query(...), user: User = CurrentUser) -> dict:
    with session_scope() as s:
        r = _repo(s, user)
        f = s.scalar(select(File).where(File.repo_id == r.id, File.path == path))
        if f is None:
            raise HTTPException(404, f"File not found: {path}")
        syms = s.scalars(
            select(Symbol).where(Symbol.repo_id == r.id, Symbol.file_path == path)
            .order_by(Symbol.start_line)
        ).all()
        return {
            "path": f.path, "language": f.language, "category": f.category,
            "loc": f.loc, "content": f.content,
            "symbols": [
                {"id": x.id, "name": x.name, "qualified_name": x.qualified_name,
                 "kind": x.kind, "start_line": x.start_line, "end_line": x.end_line,
                 "docstring": x.docstring}
                for x in syms if x.kind != "import"
            ],
        }


@router.get("/blame")
def file_blame(path: str = Query(...), user: User = CurrentUser) -> dict:
    with session_scope() as s:
        r = _repo(s, user)
        f = s.scalar(select(File).where(File.repo_id == r.id, File.path == path))
        if f is None:
            raise HTTPException(404, f"File not found: {path}")
        if not r.cloned_path:
            raise HTTPException(404, "No local clone available for blame.")
        try:
            lines = blame_file(r.cloned_path, path)
        except Exception as exc:
            raise HTTPException(404, f"Blame unavailable: {exc}") from exc
        return {"path": path, "lines": lines}


def _first_sentence(doc: str | None) -> str:
    if not doc:
        return ""
    clean = " ".join(doc.split())
    dot = clean.find(". ")
    return (clean[: dot + 1] if dot != -1 else clean)[:220]


@router.get("/symbols/index")
def symbols_index(user: User = CurrentUser) -> dict:
    """Lightweight repo-wide symbol table for the Reader's inline intelligence:
    every resolvable definition (name, signature, one-line doc, location) plus its
    fan-in count. The frontend builds name→def and id→def maps from this so hover-
    to-peek and go-to-definition resolve client-side with zero per-token round-trips."""
    with session_scope() as s:
        r = _repo(s, user)
        callers = dict(
            s.execute(
                select(SymbolEdge.dst_symbol_id, func.count())
                .where(SymbolEdge.repo_id == r.id, SymbolEdge.dst_symbol_id.is_not(None))
                .group_by(SymbolEdge.dst_symbol_id)
            ).all()
        )
        # Confidence-weighted fan-in (see indexing/graph.py) — how many of those
        # callers actually resolved with a known receiver or a unique name, as
        # opposed to an ambiguous name match. Lets the UI distinguish "used in
        # 230 places" (mostly a coincidence of the name `.get`) from real usage.
        confident = dict(
            s.execute(
                select(SymbolEdge.dst_symbol_id, func.count())
                .where(SymbolEdge.repo_id == r.id, SymbolEdge.dst_symbol_id.is_not(None),
                       SymbolEdge.confidence >= 0.8)
                .group_by(SymbolEdge.dst_symbol_id)
            ).all()
        )
        rows = s.scalars(
            select(Symbol).where(Symbol.repo_id == r.id, Symbol.kind != "import")
        ).all()
        symbols = [
            {
                "id": x.id, "name": x.name, "qualified_name": x.qualified_name,
                "kind": x.kind, "path": x.file_path,
                "line": x.start_line, "end_line": x.end_line,
                "signature": (x.signature or "").strip()[:200],
                "doc": _first_sentence(x.docstring),
                "callers": callers.get(x.id, 0),
                "callers_confident": confident.get(x.id, 0),
            }
            for x in rows
        ]
        return {"symbols": symbols}


@router.get("/symbol/{symbol_id}")
def symbol_detail(symbol_id: int, user: User = CurrentUser) -> dict:
    with session_scope() as s:
        sym = s.get(Symbol, symbol_id)
        # Ownership check — this route previously fetched ANY symbol in the
        # database by raw PK with no repo/user check at all.
        if sym is None or not _owns_repo(s, user, sym.repo_id):
            raise HTTPException(404, "Symbol not found")

        def side(edge_col, other_col):
            rows = s.execute(
                select(SymbolEdge.edge_type, SymbolEdge.confidence, Symbol)
                .join(Symbol, Symbol.id == other_col)
                .where(edge_col == symbol_id, SymbolEdge.dst_symbol_id.is_not(None))
                .order_by(SymbolEdge.confidence.desc())
            )
            return [{"id": o.id, "qualified_name": o.qualified_name, "kind": o.kind,
                     "file_path": o.file_path, "start_line": o.start_line, "edge": et,
                     "confidence": conf}
                    for et, conf, o in rows]

        callers = side(SymbolEdge.dst_symbol_id, SymbolEdge.src_symbol_id)
        callees = side(SymbolEdge.src_symbol_id, SymbolEdge.dst_symbol_id)
        return {
            "id": sym.id, "name": sym.name, "qualified_name": sym.qualified_name,
            "kind": sym.kind, "file_path": sym.file_path,
            "start_line": sym.start_line, "end_line": sym.end_line,
            "signature": sym.signature, "docstring": sym.docstring,
            "callers": callers, "callees": callees,
        }


@router.get("/graph")
def graph(level: str = Query("file"), scope: str | None = None,
          tests: bool = False, neighbors: bool = False, user: User = CurrentUser) -> dict:
    with session_scope() as s:
        r = _repo(s, user)
        if level == "symbol":
            return export_symbol_graph(s, r.id, path_prefix=scope or None,
                                       include_neighbors=neighbors)
        return export_file_graph(s, r.id, exclude_tests=not tests)


@router.get("/architecture")
def architecture(user: User = CurrentUser) -> dict:
    from archaeologist.analysis.architecture import build_architecture
    with session_scope() as s:
        r = _repo(s, user)
        return build_architecture(s, r.id, r.name)


def _clone_path(repo: Repo) -> Path:
    """Where this repo's clone lives on disk. Reads the path the ingest
    itself recorded (Repo.cloned_path) rather than recomputing owner/name
    from the URL — the clone directory is namespaced per user
    (repos/<user_id>/<owner>__<name>, see ingestion.repository.clone_or_open),
    so recomputing it here without the user id would silently point at the
    wrong (or another user's) directory."""
    if not repo.cloned_path:
        raise HTTPException(404, "No local clone available for this repo.")
    return Path(repo.cloned_path)


@router.get("/architecture/refs")
def architecture_refs(user: User = CurrentUser) -> dict:
    """Tags and recent commits of the active repo, for the delta ref picker."""
    from archaeologist.analysis import arch_delta

    with session_scope() as s:
        r = _repo(s, user)
        path = _clone_path(r)
    try:
        return arch_delta.list_refs(arch_delta.open_repo(path))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/architecture/delta")
def architecture_delta(
    base: str = Query(..., description="Ref to compare from — a tag, branch or SHA"),
    head: str = Query(..., description="Ref to compare to"),
    user: User = CurrentUser,
) -> dict:
    """Structural difference between the architecture at two refs.

    Reads two git trees and diffs their shapes; no re-ingest, no LLM, and no
    write to the database, so it is safe to call repeatedly while exploring.
    """
    from archaeologist.analysis import arch_delta

    with session_scope() as s:
        r = _repo(s, user)
        path = _clone_path(r)
        repo_id, head_sha = r.id, r.head_sha
        try:
            result = arch_delta.build_delta(path, base, head)
        except RuntimeError as exc:
            raise HTTPException(422, str(exc)) from exc
        # Arrows come from the symbol graph, which only exists for the ingested
        # commit — so they are the current wiring, drawn under whatever changed.
        # `live` says whether the compared head IS that commit, letting the UI
        # label them honestly instead of implying they were true at that ref.
        result["module_edges"] = arch_delta.module_edges(s, repo_id, result["after"])
        result["edges_live"] = bool(head_sha and result["head"]["sha"]
                                    and head_sha.startswith(result["head"]["sha"]))
    return result


@router.get("/entrypoints")
def entrypoints(user: User = CurrentUser) -> dict:
    from archaeologist.analysis.entrypoints import find_entrypoints
    with session_scope() as s:
        r = _repo(s, user)
        return {"entrypoints": find_entrypoints(s, r.id)}


@router.get("/wiki")
@limiter.limit("20/minute")
def wiki(request: Request, refresh: bool = False, user: User = CurrentUser) -> dict:
    """Generation itself now makes several LLM calls (page-structure decision +
    one prose call per page — DeepWiki's actual shape), so caching matters even
    more than before: cached per (repo, head_sha), re-ingesting naturally
    invalidates it, nothing else does. Every LLM step inside build_wiki degrades
    gracefully on its own, so this endpoint can't return a broken/empty wiki
    just because the model was unavailable or slow."""
    from archaeologist.analysis.wiki import build_wiki
    with session_scope() as s:
        r = _repo(s, user)
        if not refresh and r.wiki_cache_sha == r.head_sha and r.wiki_cache:
            return r.wiki_cache
        result = build_wiki(s, r.id, r.name, user.id)
        r.wiki_cache = result
        r.wiki_cache_sha = r.head_sha
        return result


@router.get("/folders")
def folders(user: User = CurrentUser) -> dict:
    from archaeologist.analysis.folders import folder_heat
    with session_scope() as s:
        r = _repo(s, user)
        return folder_heat(s, r.id)


@router.get("/dead-code")
def dead_code(user: User = CurrentUser) -> dict:
    from archaeologist.analysis.dead_code import find_dead_code
    with session_scope() as s:
        r = _repo(s, user)
        return find_dead_code(s, r.id)


@router.get("/communities")
def communities(user: User = CurrentUser) -> dict:
    from archaeologist.analysis.communities import find_communities
    with session_scope() as s:
        r = _repo(s, user)
        return find_communities(s, r.id)


@router.get("/coupling")
def coupling(user: User = CurrentUser) -> dict:
    from archaeologist.analysis.coupling import find_change_coupling
    with session_scope() as s:
        r = _repo(s, user)
        return find_change_coupling(s, r.id)


@router.get("/callgraph/{symbol_id}")
def callgraph(symbol_id: int, depth: int = Query(3, ge=1, le=5), user: User = CurrentUser) -> dict:
    from archaeologist.retrieval.graph_queries import call_flow
    with session_scope() as s:
        sym = s.get(Symbol, symbol_id)
        # Ownership check — previously called call_flow() on any symbol_id
        # in the database with no repo/user check at all.
        if sym is None or not _owns_repo(s, user, sym.repo_id):
            raise HTTPException(404, "Symbol not found")
        return call_flow(s, symbol_id, sym.repo_id, depth=depth)


@router.get("/impact/{symbol_id}")
def impact(symbol_id: int, user: User = CurrentUser) -> dict:
    from archaeologist.analysis.impact import analyze_impact
    with session_scope() as s:
        r = _repo(s, user)
        sym = s.get(Symbol, symbol_id)
        # This route already resolved the active repo, but never checked the
        # requested symbol actually belonged to it — a user could pass any
        # symbol_id in the database and get another user's impact analysis.
        if sym is None or sym.repo_id != r.id:
            raise HTTPException(404, "Symbol not found")
        result = analyze_impact(s, r.id, symbol_id)
        if "error" in result:
            raise HTTPException(404, result["error"])
        return result


@router.get("/export/snapshot.html", response_class=HTMLResponse)
def export_snapshot_html(user: User = CurrentUser) -> HTMLResponse:
    """A single self-contained HTML file — every core signal (architecture,
    tour, entrypoints, dead code, communities, coupling, file graph) baked in
    as static JSON. Whoever opens it needs no backend, no Docker, no LLM key —
    just a browser. Mirrors the "commit the graph, view it anywhere" pattern
    from Understand-Anything, as an explicit export instead of a git artifact."""
    from archaeologist.analysis.snapshot import build_snapshot
    from archaeologist.viz.snapshot_html import render_snapshot_html
    with session_scope() as s:
        r = _repo(s, user)
        snapshot = build_snapshot(s, r.id, r.name, user.id)
    html = render_snapshot_html(snapshot, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    # Repo.name is sanitized at ingest time (ingestion.repository.safe_repo_name)
    # for anything ingested after that fix landed, but a header value built
    # from it is cheap to harden defensively too — a stray `"` in an older
    # row's name would otherwise break out of the quoted filename parameter.
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", snapshot["repo"])[:100] or "repo"
    headers = {"Content-Disposition": f'attachment; filename="{safe_name}-snapshot.html"'}
    return HTMLResponse(content=html, headers=headers)


@router.get("/search")
def search(q: str = Query(..., max_length=2000), streams: str | None = None,
          k: int = Query(10, ge=1, le=50), user: User = CurrentUser) -> dict:
    with session_scope() as s:
        repo_id = _repo(s, user).id  # 404s cleanly if this user has no active repo
    stream_list = streams.split(",") if streams else None
    client = get_client()
    embedder = get_embedder()
    hits = search_all(client, embedder, q, repo_id, k=k, streams=stream_list)
    return {"query": q, "hits": hits}


class AskBody(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    k: int = Field(8, ge=1, le=25)
    streams: list[str] | None = Field(default=None, max_length=10)


@router.post("/ask")
@limiter.limit("20/minute")
def ask(request: Request, body: AskBody, user: User = CurrentUser) -> dict:
    with session_scope() as s:
        repo_id = _repo(s, user).id
    from archaeologist.rag.pipeline import answer_question
    try:
        res = answer_question(body.question, repo_id, user.id, k=body.k, streams=body.streams)
    except Exception:  # noqa: BLE001 - logged, never returned (see M-10 in the audit)
        # The underlying exception can carry the provider's request URL
        # (e.g. a self-hosted Ollama/Alibaba endpoint) or other config
        # detail — that must stay server-side, not go to an anonymous caller.
        _logger.exception("ask failed for repo %s", repo_id)
        raise HTTPException(500, "The LLM call failed. Check provider configuration.")
    return {"question": res.question, "answer": res.answer, "evidence": res.evidence}


class HistoryTurn(BaseModel):
    question: str = Field(..., max_length=4000)
    answer: str = Field(..., max_length=20000)


class InvestigateBody(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    # Each iteration is a full LLM call; unbounded, this is a client-directed
    # spend/DoS lever against the operator's provider key (every ask/investigate
    # route is reachable with no login — see auth.get_current_user).
    max_iterations: int = Field(2, ge=1, le=5)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=20)
    simple: bool = False


@router.post("/investigate")
@limiter.limit("15/minute")
def investigate(request: Request, body: InvestigateBody, user: User = CurrentUser) -> dict:
    with session_scope() as s:
        repo_id = _repo(s, user).id
    from archaeologist.agent.graph import investigate as run
    try:
        r = run(body.question, repo_id, user.id, max_iterations=body.max_iterations,
                history=[h.model_dump() for h in body.history], simple=body.simple)
    except Exception:  # noqa: BLE001 - logged, never returned (see M-10 in the audit)
        _logger.exception("investigate failed for repo %s", repo_id)
        raise HTTPException(500, "The investigation failed. Check provider configuration.")
    result = {"question": r["question"], "answer": r["answer"],
              "evidence": r["evidence"], "trace": r["trace"]}
    if result["answer"]:
        with session_scope() as s:
            save_conversation(s, _repo(s, user).id, "investigate", body.question, result)
    return result


@router.post("/investigate/stream")
@limiter.limit("15/minute")
def investigate_stream_endpoint(request: Request, body: InvestigateBody, user: User = CurrentUser):
    """Server-sent events: live trace steps, then the answer and evidence.
    The frontend consumes this with fetch + a ReadableStream. Saved to history
    once the stream completes with a real answer (errors aren't saved)."""
    with session_scope() as s:
        repo_id = _repo(s, user).id

    def gen():
        answer, evidence, trace = "", [], []
        for event in investigate_stream(body.question, repo_id, user.id, max_iterations=body.max_iterations,
                                        history=[h.model_dump() for h in body.history], simple=body.simple):
            etype = event.get("type")
            if etype == "answer":
                answer = event.get("answer", "")
            elif etype == "evidence":
                evidence = event.get("evidence", [])
            elif etype == "step":
                trace.append(event.get("message", ""))
            yield f"data: {json.dumps(event)}\n\n"
        if answer:
            with session_scope() as s2:
                save_conversation(s2, repo_id, "investigate", body.question,
                                   {"question": body.question, "answer": answer, "evidence": evidence, "trace": trace})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/conversations")
def conversations(kind: str = Query(...), user: User = CurrentUser) -> dict:
    with session_scope() as s:
        r = _repo(s, user)
        return {"conversations": list_conversations(s, r.id, kind)}


@router.get("/conversations/{conv_id}")
def conversation_detail(conv_id: int, user: User = CurrentUser) -> dict:
    with session_scope() as s:
        # get_conversation now enforces ownership itself (joined through
        # Conversation.repo_id -> Repo.user_id) — this route used to fetch
        # any conversation by raw PK with no check at all.
        c = get_conversation(s, conv_id, user.id)
        if c is None:
            raise HTTPException(404, "Conversation not found")
        return {"id": c.id, "kind": c.kind, "question": c.question,
                "result": c.result, "created_at": c.created_at.isoformat()}


@router.delete("/conversations/{conv_id}")
def conversation_delete(conv_id: int, user: User = CurrentUser) -> dict:
    with session_scope() as s:
        ok = delete_conversation(s, conv_id, user.id)
        if not ok:
            raise HTTPException(404, "Conversation not found")
        return {"deleted": conv_id}


class PublishConfluenceBody(BaseModel):
    section_keys: list[str] = Field(..., max_length=100)


@router.post("/confluence/publish")
def publish_confluence(body: PublishConfluenceBody, user: User = CurrentUser) -> dict:
    """Publish the reviewed wiki sections to Confluence as a tracked background
    job. Nothing is sent until this endpoint fires — the UI collects explicit
    user approval (section checklist) before calling it."""
    from archaeologist.services import confluence_job, user_integrations
    with session_scope() as s:
        integ = user_integrations.get(s, user.id)
        if not user_integrations.confluence_configured(integ):
            raise HTTPException(400, "Confluence is not connected — set it up in Settings.")
        r = _repo(s, user)
        if not r.wiki_cache:
            raise HTTPException(
                409, "Generate the wiki first (visit Start Here) before publishing.")
        repo_id = r.id
    job = confluence_job.start_publish(repo_id, body.section_keys)
    return {"job_id": job["id"], "status": job["status"]}


@router.get("/confluence/jobs/{job_id}")
def confluence_job_status(job_id: str, user: User = CurrentUser) -> dict:
    from archaeologist.services import confluence_job
    status = confluence_job.job_status(job_id)
    with session_scope() as s:
        if status is None or not _owns_repo(s, user, status["repo_id"]):
            raise HTTPException(404, f"Unknown publish job: {job_id}")
    return status


class ScanWeaknessesBody(BaseModel):
    scan_all: bool = False


@router.post("/weaknesses/scan")
@limiter.limit("5/minute")
def scan_weaknesses(request: Request, body: ScanWeaknessesBody, user: User = CurrentUser) -> dict:
    """Scan the active repo for weaknesses (LLM, one call per file, capped by
    default) as a tracked background job. Findings land in the weaknesses table
    for review — nothing external happens until tickets are explicitly approved."""
    from archaeologist.services import weakness_scan
    with session_scope() as s:
        repo_id = _repo(s, user).id
    if not llm_available():
        raise HTTPException(400, "No LLM provider available — configure one in .env to scan.")
    job = weakness_scan.start_scan(repo_id, scan_all=body.scan_all)
    return {"job_id": job["id"], "status": job["status"]}


@router.get("/weaknesses/scan")
def current_weakness_scan(user: User = CurrentUser) -> dict:
    """The active repo's current scan job, so the UI can re-attach after a
    navigation or a full page reload: the running job if one exists, else the
    most recent finished one, else null.

    The scan runs server-side in a daemon thread (persisted in
    weakness_scan_jobs), so it keeps going whether or not any client is
    watching — losing the in-page job id on unmount used to make it *look*
    stopped. This lets the page find the live job again and resume its
    progress indicator."""
    from archaeologist.services import weakness_scan
    with session_scope() as s:
        repo_id = _repo(s, user).id
    job = weakness_scan.running_job_for(repo_id) or weakness_scan.latest_job_for(repo_id)
    return {"job": job}


@router.get("/weaknesses/scan/{job_id}")
def weakness_scan_status(job_id: str, user: User = CurrentUser) -> dict:
    from archaeologist.services import weakness_scan
    status = weakness_scan.job_status(job_id)
    with session_scope() as s:
        if status is None or not _owns_repo(s, user, status["repo_id"]):
            raise HTTPException(404, f"Unknown scan job: {job_id}")
    return status


def _snippet(content: str | None, start: int, end: int, max_lines: int = 46) -> str:
    """Slice the finding's code from the indexed file on read — same approach
    (and truncation marker) as wiki.py's snippet-slicing; never stored, so it
    can't drift from File.content."""
    if not content:
        return ""
    lines = content.splitlines()
    start = max(1, start)
    end = min(len(lines), max(start, end))
    chunk = lines[start - 1:end]
    if not chunk:
        return ""
    code = "\n".join(chunk[:max_lines])
    if len(chunk) > max_lines:
        code += "\n    # … (truncated)"
    return code


_EXT_LANG = {
    "py": "python", "pyi": "python", "js": "javascript", "jsx": "javascript",
    "mjs": "javascript", "cjs": "javascript", "ts": "typescript", "tsx": "typescript",
    "go": "go", "rs": "rust", "java": "java", "rb": "ruby", "php": "php", "cs": "csharp",
    "cpp": "cpp", "cc": "cpp", "cxx": "cpp", "hpp": "cpp", "c": "c", "h": "c",
    "sh": "bash", "bash": "bash", "sql": "sql", "yaml": "yaml", "yml": "yaml",
    "json": "json", "html": "html", "css": "css", "scss": "css", "toml": "ini", "ini": "ini",
}


@router.get("/weaknesses")
def list_weaknesses(user: User = CurrentUser) -> dict:
    """All persisted findings for the active repo — durable state, unlike the
    ephemeral /dead-code candidates. Snippets are sliced from File.content here,
    at read time."""
    with session_scope() as s:
        r = _repo(s, user)
        rows = s.scalars(
            select(Weakness).where(Weakness.repo_id == r.id)
            .order_by(Weakness.status, Weakness.severity, Weakness.file_path, Weakness.start_line)
        ).all()
        files = {f.path: f.content for f in
                 s.scalars(select(File).where(File.repo_id == r.id)).all()}

        def lang_of(path: str) -> str:
            ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
            return _EXT_LANG.get(ext, "")

        return {"repo": r.name, "head_sha": r.head_sha, "weaknesses": [{
            "id": w.id,
            "file_path": w.file_path,
            "start_line": w.start_line,
            "end_line": w.end_line,
            "category": w.category,
            "severity": w.severity,
            "title": w.title,
            "description": w.description,
            "suggested_fix": w.suggested_fix,
            "status": w.status,
            "jira_url": w.jira_url,
            "head_sha": w.head_sha,
            "lang": lang_of(w.file_path),
            "snippet": _snippet(files.get(w.file_path), w.start_line, w.end_line),
        } for w in rows]}


@router.post("/weaknesses/{weakness_id}/dismiss")
def dismiss_weakness(weakness_id: int, user: User = CurrentUser) -> dict:
    """Local status flip — no job needed. A re-scan brings dismissed findings
    back (they're replaced like 'new' rows); only ticketing survives re-scans."""
    with session_scope() as s:
        r = _repo(s, user)
        w = s.get(Weakness, weakness_id)
        if w is None or w.repo_id != r.id:
            raise HTTPException(404, "Finding not found")
        if w.status == "ticketed":
            raise HTTPException(409, "Already ticketed — it can't be dismissed.")
        w.status = "dismissed"
        return {"id": weakness_id, "status": "dismissed"}


class CreateJiraTicketsBody(BaseModel):
    finding_ids: list[int] = Field(..., max_length=200)


@router.post("/jira/tickets")
def create_jira_tickets(body: CreateJiraTicketsBody, user: User = CurrentUser) -> dict:
    """Create Jira issues for the approved findings as a tracked background job.
    Nothing reaches Jira until this fires — the UI collects explicit approval
    via checkboxes first."""
    if not body.finding_ids:
        raise HTTPException(422, "Select at least one finding.")
    from archaeologist.services import jira_ticket, user_integrations
    with session_scope() as s:
        integ = user_integrations.get(s, user.id)
        if not user_integrations.jira_configured(integ):
            raise HTTPException(400, "Jira is not connected — set it up in Settings.")
        repo_id = _repo(s, user).id
    job = jira_ticket.start_tickets(repo_id, body.finding_ids)
    return {"job_id": job["id"], "status": job["status"]}


@router.get("/jira/jobs/{job_id}")
def jira_ticket_status(job_id: str, user: User = CurrentUser) -> dict:
    from archaeologist.services import jira_ticket
    status = jira_ticket.job_status(job_id)
    with session_scope() as s:
        if status is None or not _owns_repo(s, user, status["repo_id"]):
            raise HTTPException(404, f"Unknown ticket job: {job_id}")
    return status
