"""/api/codemap — query-scoped, graph-grounded codemaps. Self-contained feature."""

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from archaeologist.analysis.codemap import build_codemap, build_file_codemap, explain_edge, extend_codemap
from archaeologist.analysis.simulation import simulate_flow
from archaeologist.auth import CurrentUser
from archaeologist.models.db import session_scope
from archaeologist.models.entities import Symbol, User
from archaeologist.rate_limit import limiter
# Reuses api.py's user-scoped repo resolution and ownership check rather than
# re-deriving it — this file used to have its own two independently-written
# repo-pickers (both `order_by(Repo.id.desc())`, ignoring ingested_at) that
# could disagree with api.py's `_repo()` mid-refresh. One shared source of
# truth now.
from archaeologist.routers.api import _owns_repo, _repo
from archaeologist.services.conversations import save_conversation

router = APIRouter(prefix="/api", tags=["codemap"])


class CodemapBody(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    max_nodes: int = Field(22, ge=1, le=60)


@router.post("/codemap")
@limiter.limit("20/minute")
def codemap(request: Request, body: CodemapBody, user: User = CurrentUser) -> dict:
    with session_scope() as s:
        repo_id = _repo(s, user).id
    result = build_codemap(body.question, repo_id, user.id, max_nodes=body.max_nodes)
    if result.get("nodes"):
        with session_scope() as s:
            save_conversation(s, repo_id, "codemap", body.question, result)
    return result


@router.get("/codemap/file")
def codemap_file(path: str, max_nodes: int = Query(30, ge=1, le=60), user: User = CurrentUser) -> dict:
    """Drilling into a file in Graph lands here — a Codemap-shaped walkthrough
    of the functions/methods/classes that file actually defines, instead of
    the generic force-directed symbol graph. See build_file_codemap for why
    it skips LLM concept-cards/images by default (should be instant)."""
    with session_scope() as s:
        repo_id = _repo(s, user).id
    return build_file_codemap(path, repo_id, max_nodes=max_nodes)


def _check_owned(session, user: User, symbol_id: int) -> None:
    """Ownership check for a raw symbol id — explain-edge/extend/simulate all
    took node/symbol ids straight from the client with NO repo/user check at
    all until now (any signed-in user could probe another user's symbol ids)."""
    sym = session.get(Symbol, symbol_id)
    if sym is None or not _owns_repo(session, user, sym.repo_id):
        raise HTTPException(404, f"Symbol {symbol_id} not found")


class ExplainEdgeBody(BaseModel):
    source_id: int
    target_id: int
    question: str = Field("", max_length=4000)


@router.post("/codemap/explain-edge")
@limiter.limit("30/minute")
def codemap_explain_edge(request: Request, body: ExplainEdgeBody, user: User = CurrentUser) -> dict:
    with session_scope() as s:
        _check_owned(s, user, body.source_id)
        _check_owned(s, user, body.target_id)
    return explain_edge(body.source_id, body.target_id, body.question, user_id=user.id)


class ExtendCodemapBody(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    existing_ids: list[int] = Field(..., max_length=100)
    max_new: int = Field(10, ge=1, le=40)


@router.post("/codemap/extend")
@limiter.limit("20/minute")
def codemap_extend(request: Request, body: ExtendCodemapBody, user: User = CurrentUser) -> dict:
    with session_scope() as s:
        for symbol_id in body.existing_ids:
            _check_owned(s, user, symbol_id)
        # The existing map's own repo — every id above is already verified to
        # belong to it, so this is the correct scope for the new candidates too.
        repo_id = _repo(s, user).id
    return extend_codemap(body.question, body.existing_ids, repo_id, user.id, max_new=body.max_new)


class SimulateBody(BaseModel):
    # The walkthrough node ids, in the order the codemap laid them out — the
    # frontend already has these; the backend re-loads each symbol's real
    # source/signature so it never trusts client-sent code.
    node_ids: list[int] = Field(..., max_length=100)
    question: str = Field("", max_length=4000)


@router.post("/codemap/simulate")
@limiter.limit("15/minute")
def codemap_simulate(request: Request, body: SimulateBody, user: User = CurrentUser) -> dict:
    """▶ Play: an illustrative INPUT→TRANSFORMATION→OUTPUT trace of data flowing
    through the walkthrough. Simulated (LLM-generated representative data), never
    a real execution — see analysis/simulation.py."""
    with session_scope() as s:
        for node_id in body.node_ids:
            _check_owned(s, user, node_id)
        repo_id = _repo(s, user).id
    return simulate_flow(body.node_ids, repo_id, body.question, user_id=user.id)
