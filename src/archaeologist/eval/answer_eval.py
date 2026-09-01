"""LLM-judge evaluation of answer quality.

Two layers:
- deterministic: do the [n] citation markers in the answer reference real evidence?
- LLM judge: is every claim grounded in the cited evidence, and does it answer the
  question? Scored 1-5 with a verdict, returned as JSON.
"""

import re

from archaeologist.rag.llm import call_llm
from archaeologist.rag.pipeline import answer_question

# 'Why / how / what-breaks' questions — the archaeologist's core register.
DEFAULT_QUESTIONS = [
    "Why does Flask use an application context?",
    "What would break if I removed Flask.dispatch_request?",
    "Why did Flask move away from LocalStack for its context handling?",
    "What is the role of the sansio package in Flask?",
]

JUDGE_SYS = """You grade an answer produced by a codebase-investigation assistant.
You are given the QUESTION, the EVIDENCE it was allowed to use (numbered), and the ANSWER.
Judge ONLY against the evidence. Return ONLY JSON:
{"groundedness": 1-5,        // are all claims supported by the cited evidence?
 "citation_support": 1-5,    // are claims backed by correct [n] markers?
 "relevance": 1-5,           // does it actually answer the question?
 "unsupported_claims": <int>,// count of claims with no evidence support
 "verdict": "grounded" | "partial" | "hallucinated"}"""


def citation_validity(answer: str, n_evidence: int) -> dict:
    markers = [int(m) for m in re.findall(r"\[(\d+)\]", answer)]
    in_range = [m for m in markers if 1 <= m <= n_evidence]
    return {
        "cited": len(markers),
        "distinct": len(set(markers)),
        "valid_rate": round(len(in_range) / len(markers), 3) if markers else 0.0,
        "cited_any": bool(markers),
    }


def _evidence_block(evidence: list[dict]) -> str:
    lines = []
    for i, e in enumerate(evidence, 1):
        body = (e.get("body") or e.get("snippet") or "")[:400]
        lines.append(f"[{i}] ({e['stream']}) {e['citation']} — {e.get('title', '')}\n{body}")
    return "\n\n".join(lines)


def judge(question: str, evidence: list[dict], answer: str) -> dict:
    user = f"QUESTION:\n{question}\n\nEVIDENCE:\n{_evidence_block(evidence)}\n\nANSWER:\n{answer}"
    raw = call_llm(JUDGE_SYS, user, max_tokens=300, label="judge")
    import json
    text = raw.strip()
    if "```" in text:
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text
    a, b = text.find("{"), text.rfind("}")
    try:
        return json.loads(text[a : b + 1])
    except Exception:
        return {"groundedness": 0, "citation_support": 0, "relevance": 0,
                "unsupported_claims": -1, "verdict": "parse_error"}


def evaluate(repo_id: int, questions: list[str] | None = None) -> list[dict]:
    questions = questions or DEFAULT_QUESTIONS
    rows = []
    for q in questions:
        result = answer_question(q, repo_id, k=8)
        cv = citation_validity(result.answer, len(result.evidence))
        verdict = judge(q, result.evidence, result.answer)
        rows.append({"question": q, "n_evidence": len(result.evidence),
                     "citations": cv, "judge": verdict, "answer": result.answer})
    return rows


def aggregate(rows: list[dict]) -> dict:
    def avg(path):
        vals = [r["judge"].get(path, 0) for r in rows if isinstance(r["judge"].get(path), (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    verdicts: dict[str, int] = {}
    for r in rows:
        v = r["judge"].get("verdict", "?")
        verdicts[v] = verdicts.get(v, 0) + 1
    cite_valid = [r["citations"]["valid_rate"] for r in rows if r["citations"]["cited_any"]]
    return {
        "n": len(rows),
        "groundedness": avg("groundedness"),
        "citation_support": avg("citation_support"),
        "relevance": avg("relevance"),
        "citation_valid_rate": round(sum(cite_valid) / len(cite_valid), 3) if cite_valid else 0.0,
        "verdicts": verdicts,
    }
