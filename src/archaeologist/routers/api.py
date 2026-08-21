"""JSON API for the web frontend. Thin wrappers over the engine.

Everything the React app needs: repo summary, orientation (overview), the file
tree, file source + symbols, symbol detail (callers/callees), the dependency
graph, cross-stream search, repo management (add / refresh / job status),
and the ask/investigate endpoints (including streaming investigate).
"""

import json
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from archaeologist.agent.graph import investigate_stream
from archaeologist.config import settings
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
)
from archaeologist.retrieval.embeddings import get_embedder
from archaeologist.retrieval.multi import search_all
from archaeologist.services import ingest
from archaeologist.services.conversations import (
    delete_conversation,
    get_conversation,
    list_conversations,
    save_conversation,
)
from archaeologist.viz.export import export_file_graph, export_symbol_graph

router = APIRouter(prefix="/api", tags=["api"])


def _repo(session) -> Repo:
    """The active repo — the most recently ingested one."""
    repo = session.scalar(select(Repo).order_by(Repo.id.desc()))
    if repo is None:
        raise HTTPException(404, "No repository ingested yet.")
    return repo


def _count(session, model, repo_id) -> int:
    return session.scalar(
        select(func.count()).select_from(model).where(model.repo_id == repo_id)
    ) or 0


@router.get("/status")
def status() -> dict:
    """Runtime capabilities — which LLM/embedding providers are active. Lets the
    UI show that the app works without any API key (local Ollama / offline)."""
    from archaeologist.rag.llm import active_model, llm_available, resolve_provider
    from archaeologist.retrieval.embeddings import get_embedder

    embedder = None
    try:
        embedder = get_embedder()
    except Exception:  # noqa: BLE001 - embeddings are optional (BM25-only)
        pass
    embedding_provider = settings.embedding_provider
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
    }


@router.get("/repo")
def repo_summary() -> dict:
    with session_scope() as s:
        r = _repo(s)
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
def repos() -> dict:
    """Every ingested repo, newest first. The first entry is the active one."""
    with session_scope() as s:
        rows = s.scalars(select(Repo).order_by(Repo.id.desc())).all()
        return {"repos": [_repo_row(s, r) for r in rows]}


class AddRepoBody(BaseModel):
    url: str


@router.post("/repos")
def add_repo(body: AddRepoBody) -> dict:
    """Start a full ingest (clone → streams → symbols → indexes → graph) of a
    repo URL in the background. Returns the job; the UI polls its status.

    Idempotent: if an ingest for this URL is already running, the existing job
    is returned instead of starting a second one.
    """
    url = body.url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(422, "Enter a full repository URL, e.g. https://github.com/owner/repo")
    # A URL copied straight from a browser while viewing a branch/file (e.g.
    # ".../owner/repo/tree/main") means the repo, not a git remote — normalize
    # before it's stored/used anywhere downstream, not just at clone time.
    url = normalize_repo_url(url)
    job = ingest.start_ingest(url)
    return {"job_id": job["id"], "repo_url": url, "status": job["status"]}


@router.get("/repos/jobs/{job_id}")
def repo_job(job_id: str) -> dict:
    status = ingest.job_status(job_id)
    if status is None:
        raise HTTPException(404, f"Unknown ingest job: {job_id}")
    return status


@router.post("/repos/refresh")
def refresh_repo() -> dict:
    """Re-ingest the active repo end-to-end (picks up new commits / files)."""
    with session_scope() as s:
        r = _repo(s)
        url = r.url
    job = ingest.start_ingest(url)
    return {"job_id": job["id"], "repo_url": url, "status": job["status"]}


@router.get("/overview")
def overview() -> dict:
    with session_scope() as s:
        r = _repo(s)
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
def tree() -> dict:
    with session_scope() as s:
        r = _repo(s)
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
def file_content(path: str = Query(...)) -> dict:
    with session_scope() as s:
        r = _repo(s)
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


def _first_sentence(doc: str | None) -> str:
    if not doc:
        return ""
    clean = " ".join(doc.split())
    dot = clean.find(". ")
    return (clean[: dot + 1] if dot != -1 else clean)[:220]


@router.get("/symbols/index")
def symbols_index() -> dict:
    """Lightweight repo-wide symbol table for the Reader's inline intelligence:
    every resolvable definition (name, signature, one-line doc, location) plus its
    fan-in count. The frontend builds name→def and id→def maps from this so hover-
    to-peek and go-to-definition resolve client-side with zero per-token round-trips."""
    with session_scope() as s:
        r = _repo(s)
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
def symbol_detail(symbol_id: int) -> dict:
    with session_scope() as s:
        sym = s.get(Symbol, symbol_id)
        if sym is None:
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
          tests: bool = False, neighbors: bool = False) -> dict:
    with session_scope() as s:
        r = _repo(s)
        if level == "symbol":
            return export_symbol_graph(s, r.id, path_prefix=scope or None,
                                       include_neighbors=neighbors)
        return export_file_graph(s, r.id, exclude_tests=not tests)


@router.get("/architecture")
def architecture() -> dict:
    from archaeologist.analysis.architecture import build_architecture
    with session_scope() as s:
        r = _repo(s)
        return build_architecture(s, r.id, r.name)


@router.get("/entrypoints")
def entrypoints() -> dict:
    from archaeologist.analysis.entrypoints import find_entrypoints
    with session_scope() as s:
        r = _repo(s)
        return {"entrypoints": find_entrypoints(s, r.id)}


@router.get("/wiki")
def wiki(refresh: bool = False) -> dict:
    """Generation itself now makes several LLM calls (page-structure decision +
    one prose call per page — DeepWiki's actual shape), so caching matters even
    more than before: cached per (repo, head_sha), re-ingesting naturally
    invalidates it, nothing else does. Every LLM step inside build_wiki degrades
    gracefully on its own, so this endpoint can't return a broken/empty wiki
    just because the model was unavailable or slow."""
    from archaeologist.analysis.wiki import build_wiki
    with session_scope() as s:
        r = _repo(s)
        if not refresh and r.wiki_cache_sha == r.head_sha and r.wiki_cache:
            return r.wiki_cache
        result = build_wiki(s, r.id, r.name)
        r.wiki_cache = result
        r.wiki_cache_sha = r.head_sha
        return result


@router.get("/folders")
def folders() -> dict:
    from archaeologist.analysis.folders import folder_heat
    with session_scope() as s:
        r = _repo(s)
        return folder_heat(s, r.id)


@router.get("/dead-code")
def dead_code() -> dict:
    from archaeologist.analysis.dead_code import find_dead_code
    with session_scope() as s:
        r = _repo(s)
        return find_dead_code(s, r.id)


@router.get("/communities")
def communities() -> dict:
    from archaeologist.analysis.communities import find_communities
    with session_scope() as s:
        r = _repo(s)
        return find_communities(s, r.id)


@router.get("/coupling")
def coupling() -> dict:
    from archaeologist.analysis.coupling import find_change_coupling
    with session_scope() as s:
        r = _repo(s)
        return find_change_coupling(s, r.id)


@router.get("/callgraph/{symbol_id}")
def callgraph(symbol_id: int, depth: int = Query(3, ge=1, le=5)) -> dict:
    from archaeologist.retrieval.graph_queries import call_flow
    with session_scope() as s:
        return call_flow(s, symbol_id, depth=depth)


@router.get("/impact/{symbol_id}")
def impact(symbol_id: int) -> dict:
    from archaeologist.analysis.impact import analyze_impact
    with session_scope() as s:
        r = _repo(s)
        result = analyze_impact(s, r.id, symbol_id)
        if "error" in result:
            raise HTTPException(404, result["error"])
        return result


@router.get("/export/snapshot.html", response_class=HTMLResponse)
def export_snapshot_html() -> HTMLResponse:
    """A single self-contained HTML file — every core signal (architecture,
    tour, entrypoints, dead code, communities, coupling, file graph) baked in
    as static JSON. Whoever opens it needs no backend, no Docker, no LLM key —
    just a browser. Mirrors the "commit the graph, view it anywhere" pattern
    from Understand-Anything, as an explicit export instead of a git artifact."""
    from archaeologist.analysis.snapshot import build_snapshot
    from archaeologist.viz.snapshot_html import render_snapshot_html
    with session_scope() as s:
        r = _repo(s)
        snapshot = build_snapshot(s, r.id, r.name)
    html = render_snapshot_html(snapshot, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    headers = {"Content-Disposition": f'attachment; filename="{snapshot["repo"]}-snapshot.html"'}
    return HTMLResponse(content=html, headers=headers)


@router.get("/search")
def search(q: str = Query(...), streams: str | None = None, k: int = 10) -> dict:
    stream_list = streams.split(",") if streams else None
    client = get_client()
    embedder = get_embedder()
    hits = search_all(client, embedder, q, k=k, streams=stream_list)
    return {"query": q, "hits": hits}


class AskBody(BaseModel):
    question: str
    k: int = 8
    streams: list[str] | None = None


@router.post("/ask")
def ask(body: AskBody) -> dict:
    from archaeologist.rag.pipeline import answer_question
    try:
        res = answer_question(body.question, k=body.k, streams=body.streams)
    except Exception as exc:  # noqa: BLE001 - surface a structured error to the UI
        raise HTTPException(500, f"The LLM call failed: {exc}") from exc
    return {"question": res.question, "answer": res.answer, "evidence": res.evidence}


class HistoryTurn(BaseModel):
    question: str
    answer: str


class InvestigateBody(BaseModel):
    question: str
    max_iterations: int = 2
    history: list[HistoryTurn] = []
    simple: bool = False


@router.post("/investigate")
def investigate(body: InvestigateBody) -> dict:
    from archaeologist.agent.graph import investigate as run
    try:
        r = run(body.question, max_iterations=body.max_iterations,
                history=[h.model_dump() for h in body.history], simple=body.simple)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"The investigation failed: {exc}") from exc
    result = {"question": r["question"], "answer": r["answer"],
              "evidence": r["evidence"], "trace": r["trace"]}
    if result["answer"]:
        with session_scope() as s:
            save_conversation(s, _repo(s).id, "investigate", body.question, result)
    return result


@router.post("/investigate/stream")
def investigate_stream_endpoint(body: InvestigateBody):
    """Server-sent events: live trace steps, then the answer and evidence.
    The frontend consumes this with fetch + a ReadableStream. Saved to history
    once the stream completes with a real answer (errors aren't saved)."""
    with session_scope() as s:
        repo_id = _repo(s).id

    def gen():
        answer, evidence, trace = "", [], []
        for event in investigate_stream(body.question, max_iterations=body.max_iterations,
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
def conversations(kind: str = Query(...)) -> dict:
    with session_scope() as s:
        r = _repo(s)
        return {"conversations": list_conversations(s, r.id, kind)}


@router.get("/conversations/{conv_id}")
def conversation_detail(conv_id: int) -> dict:
    with session_scope() as s:
        c = get_conversation(s, conv_id)
        if c is None:
            raise HTTPException(404, "Conversation not found")
        return {"id": c.id, "kind": c.kind, "question": c.question,
                "result": c.result, "created_at": c.created_at.isoformat()}


@router.delete("/conversations/{conv_id}")
def conversation_delete(conv_id: int) -> dict:
    with session_scope() as s:
        ok = delete_conversation(s, conv_id)
        if not ok:
            raise HTTPException(404, "Conversation not found")
        return {"deleted": conv_id}
