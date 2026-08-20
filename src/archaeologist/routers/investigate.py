"""/investigate endpoint — the full agentic investigation (plan → retrieve → grade → synthesize)."""

from fastapi import APIRouter
from pydantic import BaseModel

from archaeologist.agent.graph import investigate

router = APIRouter(tags=["investigate"])


class InvestigateRequest(BaseModel):
    question: str
    max_iterations: int = 2


class InvestigateResponse(BaseModel):
    question: str
    answer: str
    evidence: list[dict]
    trace: list[str]


@router.post("/investigate", response_model=InvestigateResponse)
def investigate_endpoint(req: InvestigateRequest) -> InvestigateResponse:
    result = investigate(req.question, max_iterations=req.max_iterations)
    return InvestigateResponse(
        question=result["question"],
        answer=result["answer"],
        evidence=result["evidence"],
        trace=result["trace"],
    )
