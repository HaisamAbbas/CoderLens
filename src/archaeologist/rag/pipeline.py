"""The Phase 5 RAG pipeline: retrieve cross-stream evidence, then have Claude
synthesize an evidence-cited answer.

`retrieve()` works with no API key (useful for inspecting what would be sent);
`answer_question()` adds the Claude generation step.
"""

from dataclasses import dataclass, field

from archaeologist.indexing.opensearch_client import get_client
from archaeologist.rag import prompts
from archaeologist.rag.llm import call_llm, llm_available
from archaeologist.retrieval.embeddings import get_embedder
from archaeologist.retrieval.multi import search_all


@dataclass
class RagResult:
    question: str
    answer: str
    evidence: list[dict] = field(default_factory=list)


def retrieve(question: str, repo_id: int, k: int = 8, streams: list[str] | None = None) -> list[dict]:
    client = get_client()
    embedder = get_embedder()
    return search_all(client, embedder, question, repo_id, k=k, streams=streams)


def answer_question(
    question: str, repo_id: int, k: int = 8, streams: list[str] | None = None, max_tokens: int = 1024
) -> RagResult:
    evidence = retrieve(question, repo_id, k=k, streams=streams)
    if not evidence:
        return RagResult(question, "No evidence found in the indexed repository.", [])

    if not llm_available():
        # No LLM at all (no key, no local model) — return the evidence itself,
        # formatted as an extractive, fully-cited digest.
        return RagResult(question, prompts.build_digest(question, evidence), evidence)

    prompt = prompts.build_prompt(question, evidence)
    answer = call_llm(prompts.SYSTEM, prompt, max_tokens=max_tokens)
    return RagResult(question, answer, evidence)
