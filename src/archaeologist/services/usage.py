"""Operator-facing usage tracking (Phase 5 of the multi-user migration).

Logs the estimated cost of every LLM/embedding call to `UsageLedger`, broken
down per user, purely for the operator to review later — there is NO
enforcement here: nothing checks a budget, nothing raises, nothing slows a
request down. LLM/embedding cost is the operator's to fund, not the
client's (see TRACKING.md), so this is visibility only.

Token counts are ESTIMATED from character length (~3.5 chars/token — the
same ratio `retrieval/embeddings.py`'s OpenRouterEmbedder already uses for
its truncation math), not each provider's real reported usage. Getting real
per-provider counts would mean changing what all 7 `_call_X` functions in
rag/llm.py return; not worth that surface area for a visibility-only
feature with no number riding on it besides a dashboard.
"""

from math import ceil

from archaeologist.models.db import session_scope
from archaeologist.models.entities import UsageLedger
from archaeologist.rag import pricing

CHARS_PER_TOKEN = 3.5


def _estimate_tokens(n_chars: int) -> int:
    return ceil(max(0, n_chars) / CHARS_PER_TOKEN)


def record(
    user_id: int | None, kind: str, provider: str, model: str, label: str,
    prompt_chars: int, completion_chars: int,
) -> None:
    """Best-effort: a logging failure must never break the call it's attached
    to. `user_id=None` (a CLI/eval/notebook caller with no signed-in user)
    silently skips recording — there's no one to attribute the cost to."""
    if user_id is None:
        return
    try:
        prompt_tokens = _estimate_tokens(prompt_chars)
        completion_tokens = _estimate_tokens(completion_chars)
        cost = pricing.estimate_cost(provider, model, prompt_tokens, completion_tokens)
        with session_scope() as session:
            session.add(UsageLedger(
                user_id=user_id, kind=kind, provider=provider, model=model, label=label,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                estimated=True, cost_usd=cost,
            ))
    except Exception:  # noqa: BLE001 - visibility must never break what it's watching
        pass
