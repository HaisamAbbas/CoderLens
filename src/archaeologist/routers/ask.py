"""/ask endpoint — evidence-cited answers over the indexed repository."""

from fastapi import APIRouter
from pydantic import BaseModel

from archaeologist.rag.pipeline import answer_question

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: str
    k: int = 8
    streams: list[str] | None = None


class AskResponse(BaseModel):
    question: str
    answer: str
    evidence: list[dict]


@router.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    result = answer_question(req.question, k=req.k, streams=req.streams)
    return AskResponse(question=result.question, answer=result.answer, evidence=result.evidence)
