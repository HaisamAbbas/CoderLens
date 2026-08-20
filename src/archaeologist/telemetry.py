"""Lightweight in-process telemetry for LLM calls.

Records provider/model/latency/size for every `call_llm`, so the eval harness
and notebooks can report cost/latency without any external service. If Langfuse
credentials are present it also mirrors events there (best-effort, optional).
"""

import time

from archaeologist.config import settings

_CALLS: list[dict] = []


def record_llm(provider: str, model: str, latency_ms: float,
               in_chars: int, out_chars: int, label: str = "") -> None:
    event = {
        "provider": provider, "model": model, "latency_ms": round(latency_ms),
        "in_chars": in_chars, "out_chars": out_chars, "label": label,
    }
    _CALLS.append(event)
    _maybe_langfuse(event)


def reset() -> None:
    _CALLS.clear()


def calls() -> list[dict]:
    return list(_CALLS)


def summary() -> dict:
    if not _CALLS:
        return {"calls": 0}
    total = sum(c["latency_ms"] for c in _CALLS)
    return {
        "calls": len(_CALLS),
        "total_latency_ms": round(total),
        "avg_latency_ms": round(total / len(_CALLS)),
        "total_out_chars": sum(c["out_chars"] for c in _CALLS),
    }


class timed:
    """Context manager returning elapsed ms via `.ms`."""

    def __enter__(self):
        self._t = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = (time.perf_counter() - self._t) * 1000.0
        return False


def _maybe_langfuse(event: dict) -> None:
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return
    try:  # optional dependency; no-op if not installed
        from langfuse import Langfuse

        lf = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        lf.trace(name="llm_call", metadata=event)
    except Exception:
        pass  # observability must never break the app
