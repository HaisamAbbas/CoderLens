"""Pluggable LLM wrapper for the RAG/agent layers.

Provider resolution (`settings.llm_provider`):
- \"auto\" (default): a hosted key if set (Gemini, then Anthropic), else a local
  Ollama model (no API key, no cost), else None → callers run their offline /
  retrieval-only fallbacks. Users paste a repo URL and work with zero keys.
- explicit \"gemini\" / \"anthropic\" / \"ollama\": that provider only.

Every provider exposes the same `call_llm(system, user)` surface so callers stay
provider-agnostic.
"""

import json
import time

from archaeologist.config import settings

_ollama_checked_at = 0.0
_ollama_ok = False


def ollama_available() -> bool:
    """Is a local Ollama server reachable? Result is cached for 30s so the
    frequent availability checks don't add a network round-trip each time."""
    global _ollama_checked_at, _ollama_ok
    now = time.monotonic()
    if now - _ollama_checked_at < 30:
        return _ollama_ok
    _ollama_checked_at = now
    try:
        import httpx

        resp = httpx.get(
            f"{settings.ollama_base_url.rstrip('/')}/api/tags", timeout=2.0
        )
        _ollama_ok = resp.status_code == 200
    except Exception:  # noqa: BLE001 - any failure means "not available"
        _ollama_ok = False
    return _ollama_ok


def resolve_provider() -> str | None:
    """The provider actually in use right now, or None when no LLM is available."""
    raw = settings.llm_provider.strip().lower()
    if raw == "gemini":
        return "gemini" if settings.gemini_api_key else None
    if raw == "anthropic":
        return "anthropic" if settings.anthropic_api_key else None
    if raw == "openrouter":
        return "openrouter" if settings.openrouter_api_key else None
    if raw == "alibaba":
        return "alibaba" if settings.alibaba_api_key and settings.alibaba_base_url else None
    if raw == "aihubmix":
        return "aihubmix" if settings.aihubmix_api_key else None
    if raw == "ollama":
        return "ollama" if ollama_available() else None
    # "auto" (or empty): hosted key first, then local, then offline.
    if settings.gemini_api_key:
        return "gemini"
    if settings.anthropic_api_key:
        return "anthropic"
    if settings.openrouter_api_key:
        return "openrouter"
    if settings.alibaba_api_key and settings.alibaba_base_url:
        return "alibaba"
    if settings.aihubmix_api_key:
        return "aihubmix"
    return "ollama" if ollama_available() else None


def llm_available() -> bool:
    return resolve_provider() is not None


# Backwards-compatible alias (used by notebooks / older call sites).
def has_api_key() -> bool:
    return llm_available()


def active_model() -> str:
    provider = resolve_provider()
    if provider == "gemini":
        return settings.gemini_model
    if provider == "anthropic":
        return settings.claude_model
    if provider == "openrouter":
        return settings.openrouter_model
    if provider == "alibaba":
        return settings.alibaba_model
    if provider == "aihubmix":
        return settings.aihubmix_model
    if provider == "ollama":
        return settings.ollama_model
    return "none"


def call_llm(system: str, user: str, max_tokens: int = 1024, temperature: float = 0.0,
             label: str = "") -> str:
    from archaeologist import telemetry

    provider = resolve_provider()
    if provider is None:
        raise RuntimeError(
            "No LLM provider available. Set GEMINI_API_KEY / ANTHROPIC_API_KEY, "
            "or start the local model with `docker compose up -d` (Ollama, no key)."
        )
    with telemetry.timed() as t:
        if provider == "gemini":
            out = _call_gemini(system, user, max_tokens, temperature)
        elif provider == "anthropic":
            out = _call_claude(system, user, max_tokens, temperature)
        elif provider == "openrouter":
            out = _call_openrouter(system, user, max_tokens, temperature)
        elif provider == "alibaba":
            out = _call_alibaba(system, user, max_tokens, temperature)
        elif provider == "aihubmix":
            out = _call_aihubmix(system, user, max_tokens, temperature)
        elif provider == "ollama":
            out = _call_ollama(system, user, max_tokens, temperature)
        else:  # pragma: no cover - resolve_provider guards this
            raise RuntimeError(f"Unknown llm provider: {provider!r}")
    telemetry.record_llm(provider, active_model(), t.ms, len(system) + len(user), len(out), label)
    return out


def parse_llm_json(raw: str, default: dict | None = None) -> dict:
    """Extract a JSON object from LLM output — the one shared parser every
    structured-prompt caller uses (prompts ask for {"key": [...]} objects, so
    this slices {...} only, never a bare top-level array). Tolerates markdown
    code fences and surrounding prose; returns `default` (or {}) on failure."""
    text = raw.strip()
    if "```" in text:  # strip markdown fences
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return default if default is not None else {}


def _call_gemini(system: str, user: str, max_tokens: int, temperature: float) -> str:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not set in .env")
    from google import genai
    from google.genai import errors as gerr
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key)
    config = types.GenerateContentConfig(
        system_instruction=system, max_output_tokens=max_tokens, temperature=temperature
    )
    for attempt in range(6):
        try:
            response = client.models.generate_content(
                model=settings.gemini_model, contents=user, config=config
            )
            return response.text or ""
        except gerr.APIError as exc:  # 429 rate limit or 5xx overload → back off and retry
            if getattr(exc, "code", None) in (429, 500, 502, 503) and attempt < 5:
                time.sleep(min(60, 18 * (attempt + 1)))
                continue
            raise
    return ""


def _call_claude(system: str, user: str, max_tokens: int, temperature: float) -> str:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in .env")
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    message = client.messages.create(
        model=settings.claude_model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in message.content if block.type == "text")


def _call_openrouter(system: str, user: str, max_tokens: int, temperature: float) -> str:
    """OpenRouter — OpenAI-compatible chat completions, one key routes to many
    models. Free (":free"-suffixed) models are rate-limited (e.g. 50 req/day on
    a free account), so a 429 here almost always means that daily cap, not a
    transient overload — back off briefly and retry a couple of times, but
    don't hold a connection/session open for minutes the way Gemini's backoff
    can (see analysis/codemap.py for why that matters).

    Many current free models are hybrid-reasoning: they spend hidden
    "thinking" tokens before writing the visible answer, and those tokens
    count against `max_tokens` — a caller asking for a 700-token answer can
    get cut off with an empty `content` if the model reasoned through most of
    that budget first (observed directly: gpt-oss-20b with max_tokens=20
    returned content=null, finish_reason="length", 17 of 20 tokens spent on
    reasoning). `reasoning: {"exclude": true}` keeps the trace out of the
    response either way, and padding the request budget gives the model room
    to think *and* answer without the caller having to know or care that the
    active model reasons at all."""
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set in .env")
    import httpx

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "HTTP-Referer": "https://github.com/archaeologist",
        "X-Title": "CoderLens",
    }
    payload = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens + 600,
        "temperature": temperature,
        "reasoning": {"exclude": True},
    }
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=90.0)
            if resp.status_code == 429 and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            return (choice.get("message") or {}).get("content") or ""
        except Exception as exc:  # noqa: BLE001 - retry transient failures
            last_exc = exc
            if attempt == 2:
                break
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"OpenRouter request failed: {last_exc}") from last_exc


def _call_alibaba(system: str, user: str, max_tokens: int, temperature: float) -> str:
    """Alibaba Cloud Model Studio (Qwen) — OpenAI-compatible chat completions.
    The base URL is workspace-specific (shown once when the API key is
    created, e.g. `https://ws-xxxx.<region>.maas.aliyuncs.com/compatible-mode/v1`)
    — there's no shared default host the way OpenRouter/Gemini have."""
    if not settings.alibaba_api_key or not settings.alibaba_base_url:
        raise RuntimeError("ALIBABA_API_KEY / ALIBABA_BASE_URL not set in .env")
    import httpx

    url = f"{settings.alibaba_base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.alibaba_api_key}"}
    payload = {
        "model": settings.alibaba_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=90.0)
            if resp.status_code == 429 and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            return (choice.get("message") or {}).get("content") or ""
        except Exception as exc:  # noqa: BLE001 - retry transient failures
            last_exc = exc
            if attempt == 2:
                break
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Alibaba Model Studio request failed: {last_exc}") from last_exc


_AIHUBMIX_URL = "https://aihubmix.com/v1"


def _call_aihubmix(system: str, user: str, max_tokens: int, temperature: float) -> str:
    """AIHubMix — OpenAI-compatible aggregator, one key routes to many hosted
    models (GLM, Qwen, OpenAI, Claude, ...) behind a single fixed public
    endpoint, unlike Alibaba's workspace-specific URL."""
    if not settings.aihubmix_api_key:
        raise RuntimeError("AIHUBMIX_API_KEY not set in .env")
    import httpx

    url = f"{_AIHUBMIX_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.aihubmix_api_key}"}
    payload = {
        "model": settings.aihubmix_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=90.0)
            if resp.status_code == 429 and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            return (choice.get("message") or {}).get("content") or ""
        except Exception as exc:  # noqa: BLE001 - retry transient failures
            last_exc = exc
            if attempt == 2:
                break
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"AIHubMix request failed: {last_exc}") from last_exc


def call_llm_stream(system: str, user: str, max_tokens: int = 1024, temperature: float = 0.0,
                     label: str = ""):
    """Like `call_llm`, but yields text deltas as they arrive instead of
    returning one blocking string. True token streaming is implemented for the
    OpenAI-compatible providers (alibaba, aihubmix, openrouter) and ollama;
    gemini/anthropic/unknown fall back to a single chunk containing the full
    answer (a correct, if non-incremental, degradation — callers just see one
    big delta instead of many small ones)."""
    from archaeologist import telemetry

    provider = resolve_provider()
    if provider is None:
        raise RuntimeError(
            "No LLM provider available. Set GEMINI_API_KEY / ANTHROPIC_API_KEY, "
            "or start the local model with `docker compose up -d` (Ollama, no key)."
        )
    out_len = 0
    with telemetry.timed() as t:
        if provider == "alibaba":
            gen = _stream_openai_compat(
                f"{settings.alibaba_base_url.rstrip('/')}/chat/completions",
                {"Authorization": f"Bearer {settings.alibaba_api_key}"},
                settings.alibaba_model, system, user, max_tokens, temperature,
            )
        elif provider == "aihubmix":
            gen = _stream_openai_compat(
                f"{_AIHUBMIX_URL}/chat/completions",
                {"Authorization": f"Bearer {settings.aihubmix_api_key}"},
                settings.aihubmix_model, system, user, max_tokens, temperature,
            )
        elif provider == "openrouter":
            gen = _stream_openai_compat(
                "https://openrouter.ai/api/v1/chat/completions",
                {"Authorization": f"Bearer {settings.openrouter_api_key}",
                 "HTTP-Referer": "https://github.com/archaeologist", "X-Title": "CoderLens"},
                settings.openrouter_model, system, user, max_tokens + 600, temperature,
                extra={"reasoning": {"exclude": True}},
            )
        elif provider == "ollama":
            gen = _stream_ollama(system, user, max_tokens, temperature)
        else:
            # gemini / anthropic: no streaming client wired up yet — yield the
            # whole answer as one chunk so callers still get a correct result.
            text = call_llm(system, user, max_tokens, temperature, label=label)
            out_len = len(text)
            yield text
            gen = None
        if gen is not None:
            for chunk in gen:
                out_len += len(chunk)
                yield chunk
    telemetry.record_llm(provider, active_model(), t.ms, len(system) + len(user), out_len, label)


def _stream_openai_compat(url: str, headers: dict, model: str, system: str, user: str,
                          max_tokens: int, temperature: float, extra: dict | None = None):
    """SSE token streaming for any OpenAI-compatible chat completions endpoint
    (alibaba, openrouter). Yields `delta.content` strings as they arrive."""
    import httpx

    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens, "temperature": temperature, "stream": True,
        **(extra or {}),
    }
    with httpx.Client(timeout=90.0) as client:
        with client.stream("POST", url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[len("data: "):].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                    delta = (obj.get("choices") or [{}])[0].get("delta", {}).get("content")
                except (json.JSONDecodeError, IndexError, AttributeError):
                    continue
                if delta:
                    yield delta


def _stream_ollama(system: str, user: str, max_tokens: int, temperature: float):
    """NDJSON token streaming for Ollama's /api/chat (`stream: true`)."""
    import httpx

    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
    payload = {
        "model": settings.ollama_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": True,
        "options": {"num_predict": max_tokens, "temperature": temperature},
    }
    with httpx.Client(timeout=300.0) as client:
        with client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    delta = (obj.get("message") or {}).get("content")
                except json.JSONDecodeError:
                    continue
                if delta:
                    yield delta


def _call_ollama(system: str, user: str, max_tokens: int, temperature: float) -> str:
    """Local model via Ollama's /api/chat — no API key, no cost. Local CPU
    inference is slow, so the request timeout is generous."""
    import httpx

    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
    payload = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"num_predict": max_tokens, "temperature": temperature},
    }
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = httpx.post(url, json=payload, timeout=300.0)
            resp.raise_for_status()
            return (resp.json().get("message") or {}).get("content") or ""
        except Exception as exc:  # noqa: BLE001 - retry transient failures
            last_exc = exc
            if attempt == 2:
                break
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Ollama request failed: {last_exc}") from last_exc
