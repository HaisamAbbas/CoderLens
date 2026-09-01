"""Static $/M-token pricing for the Phase 5 usage ledger (operator-facing
cost visibility — see services/usage.py; there is no enforcement here).

Only figures actually confirmed live in this project are hardcoded (see
.env.example's comments — verified via real API responses while wiring up
each provider this session), rather than guessed public list prices that
could silently understate real spend. Anything not listed here — including
providers we simply haven't priced yet (gemini, anthropic, alibaba) — falls
back to the most expensive KNOWN paid price in this table, never $0, so an
untracked model can't look artificially free.

Figures are $ per 1,000,000 tokens, as (input, output).
"""

# Confirmed live this session — see .env.example.
_KNOWN: dict[tuple[str, str], tuple[float, float]] = {
    ("zai", "glm-5.3-flash"): (0.075, 0.25),
    ("aihubmix", "glm-5.3-flash"): (0.11, 0.39),
    ("aihubmix", "minimax-m3-free"): (0.0, 0.0),
}

# Structurally free regardless of model: self-hosted, no API call at all.
_FREE_PROVIDERS = {"ollama"}


def _is_free_openrouter_model(model: str) -> bool:
    # OpenRouter's own convention: a ":free"-suffixed model id is rate-limited
    # but genuinely $0, confirmed live (see rag/llm.py's OpenRouter provider).
    return model.endswith(":free")


def price_per_million(provider: str, model: str) -> tuple[float, float]:
    """(input $/M, output $/M) for one provider+model."""
    if provider in _FREE_PROVIDERS:
        return (0.0, 0.0)
    if provider == "openrouter" and _is_free_openrouter_model(model):
        return (0.0, 0.0)
    key = (provider, model)
    if key in _KNOWN:
        return _KNOWN[key]
    paid = [v for v in _KNOWN.values() if v != (0.0, 0.0)]
    return max(paid, key=lambda v: v[0] + v[1]) if paid else (0.0, 0.0)


def estimate_cost(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float:
    in_price, out_price = price_per_million(provider, model)
    return (prompt_tokens * in_price + completion_tokens * out_price) / 1_000_000
