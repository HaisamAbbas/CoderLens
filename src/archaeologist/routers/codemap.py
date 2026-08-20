"""/api/codemap — query-scoped, graph-grounded codemaps. Self-contained feature."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from archaeologist.analysis.codemap import build_codemap, build_file_codemap, explain_edge, extend_codemap
from archaeologist.analysis.simulation import simulate_flow
from archaeologist.models.db import session_scope
from archaeologist.models.entities import Repo
from archaeologist.services.conversations import save_conversation

router = APIRouter(prefix="/api", tags=["codemap"])


class CodemapBody(BaseModel):
    question: str
    max_nodes: int = 22


@router.post("/codemap")
def codemap(body: CodemapBody) -> dict:
    result = build_codemap(body.question, max_nodes=body.max_nodes)
    if result.get("nodes"):
        with session_scope() as s:
            repo = s.scalar(select(Repo).order_by(Repo.id.desc()))
            if repo is not None:
                save_conversation(s, repo.id, "codemap", body.question, result)
    return result


@router.get("/codemap/file")
def codemap_file(path: str, max_nodes: int = 30) -> dict:
    """Drilling into a file in Graph lands here — a Codemap-shaped walkthrough
    of the functions/methods/classes that file actually defines, instead of
    the generic force-directed symbol graph. See build_file_codemap for why
    it skips LLM concept-cards/images by default (should be instant)."""
    with session_scope() as s:
        repo = s.scalar(select(Repo).order_by(Repo.id.desc()))
        if repo is None:
            raise HTTPException(404, "No repo ingested.")
        repo_id = repo.id
    return build_file_codemap(path, repo_id, max_nodes=max_nodes)


class ExplainEdgeBody(BaseModel):
    source_id: int
    target_id: int
    question: str = ""


@router.post("/codemap/explain-edge")
def codemap_explain_edge(body: ExplainEdgeBody) -> dict:
    return explain_edge(body.source_id, body.target_id, body.question)


class ExtendCodemapBody(BaseModel):
    question: str
    existing_ids: list[int]
    max_new: int = 10


@router.post("/codemap/extend")
def codemap_extend(body: ExtendCodemapBody) -> dict:
    return extend_codemap(body.question, body.existing_ids, max_new=body.max_new)


class SimulateBody(BaseModel):
    # The walkthrough node ids, in the order the codemap laid them out — the
    # frontend already has these; the backend re-loads each symbol's real
    # source/signature so it never trusts client-sent code.
    node_ids: list[int]
    question: str = ""


@router.post("/codemap/simulate")
def codemap_simulate(body: SimulateBody) -> dict:
    """▶ Play: an illustrative INPUT→TRANSFORMATION→OUTPUT trace of data flowing
    through the walkthrough. Simulated (LLM-generated representative data), never
    a real execution — see analysis/simulation.py."""
    return simulate_flow(body.node_ids, body.question)
