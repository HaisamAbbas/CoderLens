"""Embeddings for semantic search — pluggable by `settings.embedding_provider`.

- "local"   : fastembed (ONNX, e.g. BGE-small). Offline, no API key, no rate
              limits — but loads a real model into THIS process's memory,
              which is tight on a free-tier (512MB) deploy.
- "voyage"  : Voyage AI (code-tuned). Hosted; free tier is heavily rate-limited.
- "alibaba" : Alibaba Cloud Model Studio, hosted, OpenAI-compatible endpoint —
              reuses the same account already paying for the LLM. No model
              loaded in-process, so it's the one that actually helps the
              memory-constrained deploy case.
- "aihubmix": AIHubMix aggregator, hosted, OpenAI-compatible, fixed public
              endpoint (no per-workspace URL). Reuses aihubmix_api_key.
- "openrouter": OpenRouter, hosted, OpenAI-compatible. The default model is
              genuinely free but caps input at 512 tokens/text (hard error,
              not silent truncation) — long symbol bodies get truncated
              before embedding. Reuses openrouter_api_key.

Every embedder exposes `.dim`, `.embed_documents(texts)`, and `.embed_query(text)`,
so the indexer and retriever don't care which one is active.
"""

import time

import httpx

from archaeologist.config import settings

EMBED_BATCH = 32
MAX_RETRIES = 8

# Stand-in for a text with nothing in it. OpenAI-compatible /embeddings
# endpoints reject an empty string outright — OpenRouter answers 400
# "expected string to have >=1 characters" — and because the request carries a
# whole batch, one blank text fails all 32 of them. That aborted an entire
# ingest at the code-index step over a single symbol with no extractable body.
# Substituting keeps every response aligned one-to-one with its input list.
_BLANK_INPUT = "(empty)"


def _safe_inputs(texts: list[str]) -> list[str]:
    return [t if t.strip() else _BLANK_INPUT for t in texts]


def _check(resp: httpx.Response, provider: str) -> None:
    """Raise with the provider's actual complaint attached.

    httpx's raise_for_status() reports only the status line and the URL, so
    what the provider actually objected to — unknown model id, quota gone,
    malformed input — never made it into the job's error field, leaving a bare
    "400 Bad Request" with nothing to act on.
    """
    if resp.is_success:
        return
    raise RuntimeError(f"{provider} embeddings: HTTP {resp.status_code} — {resp.text[:500]}")


class LocalEmbedder:
    """fastembed ONNX model — downloaded once, then fully local."""

    def __init__(self) -> None:
        from fastembed import TextEmbedding

        self.model_name = settings.local_embedding_model
        self._model = TextEmbedding(model_name=self.model_name)
        self.dim = len(next(iter(self._model.embed(["probe"]))))

    def embed_documents(self, texts: list[str], progress: bool = True) -> list[list[float]]:
        out: list[list[float]] = []
        total = len(texts)
        for i, vec in enumerate(self._model.embed(texts, batch_size=64)):
            out.append([float(x) for x in vec])
            if progress and (i + 1) % 128 == 0:
                print(f"      embedded {i + 1}/{total}", flush=True)
        return out

    def embed_query(self, text: str) -> list[float]:
        return [float(x) for x in next(iter(self._model.embed([text])))]


class VoyageEmbedder:
    """Voyage AI hosted embeddings; asymmetric doc/query input types."""

    def __init__(self) -> None:
        import voyageai

        if not settings.voyage_api_key:
            raise RuntimeError("VOYAGE_API_KEY not set in .env")
        self.client = voyageai.Client(api_key=settings.voyage_api_key)
        self.model = settings.voyage_model
        self.dim = settings.voyage_dim
        self.delay = settings.voyage_request_delay

    def _embed(self, texts: list[str], input_type: str, progress: bool = False) -> list[list[float]]:
        out: list[list[float]] = []
        n = len(texts)
        for start in range(0, n, EMBED_BATCH):
            chunk = texts[start : start + EMBED_BATCH]
            for attempt in range(MAX_RETRIES):
                try:
                    result = self.client.embed(chunk, model=self.model, input_type=input_type)
                    out.extend(result.embeddings)
                    break
                except Exception:
                    if attempt == MAX_RETRIES - 1:
                        raise
                    time.sleep(min(60, 15 * (attempt + 1)))
            if progress:
                print(f"      embedded {min(start + EMBED_BATCH, n)}/{n}", flush=True)
            if self.delay and (start + EMBED_BATCH) < n:
                time.sleep(self.delay)
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "document", progress=True)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "query")[0]


class AlibabaEmbedder:
    """Alibaba Cloud Model Studio hosted embeddings (OpenAI-compatible
    `/embeddings` endpoint) — no local model, so no in-process memory cost.
    Confirmed live: text-embedding-v3/v4 both work and return real 1024-dim
    vectors; text-embedding-v2 is access-denied on at least some workspaces.
    """

    # Alibaba's API caps input at 10 texts per call (confirmed live) —
    # unrelated to EMBED_BATCH, which is sized for providers with no such cap.
    _MAX_PER_CALL = 10

    def __init__(self) -> None:
        if not settings.alibaba_api_key or not settings.alibaba_base_url:
            raise RuntimeError("ALIBABA_API_KEY / ALIBABA_BASE_URL not set in .env")
        self.model = settings.alibaba_embedding_model
        self.dim = settings.alibaba_embedding_dim
        self._url = f"{settings.alibaba_base_url.rstrip('/')}/embeddings"
        self._headers = {"Authorization": f"Bearer {settings.alibaba_api_key}"}

    def _embed(self, texts: list[str], progress: bool = False) -> list[list[float]]:
        out: list[list[float]] = []
        n = len(texts)
        with httpx.Client(timeout=30.0) as client:
            for start in range(0, n, self._MAX_PER_CALL):
                chunk = _safe_inputs(texts[start : start + self._MAX_PER_CALL])
                payload = {"model": self.model, "input": chunk, "dimensions": self.dim}
                for attempt in range(MAX_RETRIES):
                    resp = client.post(self._url, json=payload, headers=self._headers)
                    if resp.status_code == 429:
                        time.sleep(min(60, 5 * (attempt + 1)))
                        continue
                    _check(resp, "Alibaba")
                    data = sorted(resp.json()["data"], key=lambda d: d["index"])
                    out.extend(d["embedding"] for d in data)
                    break
                else:
                    raise RuntimeError(
                        f"Alibaba embeddings: still rate-limited after {MAX_RETRIES} retries "
                        f"at batch {start} — {resp.text[:300]}"
                    )
                if progress:
                    print(f"      embedded {min(start + self._MAX_PER_CALL, n)}/{n}", flush=True)
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, progress=True)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


class AihubmixEmbedder:
    """AIHubMix hosted embeddings (OpenAI-compatible `/embeddings` endpoint,
    fixed public URL — unlike Alibaba's workspace-specific one). Reuses
    aihubmix_api_key, the same key already paying for the LLM. No `dimensions`
    param sent — unlike Alibaba's Matryoshka-configurable models, the default
    embedding model here (jina-embeddings-v2-base-code) doesn't support
    truncation, so the dimension is whatever the model naturally returns."""

    def __init__(self) -> None:
        if not settings.aihubmix_api_key:
            raise RuntimeError("AIHUBMIX_API_KEY not set in .env")
        self.model = settings.aihubmix_embedding_model
        self.dim = settings.aihubmix_embedding_dim
        self._url = "https://aihubmix.com/v1/embeddings"
        self._headers = {"Authorization": f"Bearer {settings.aihubmix_api_key}"}

    def _embed(self, texts: list[str], progress: bool = False) -> list[list[float]]:
        out: list[list[float]] = []
        n = len(texts)
        with httpx.Client(timeout=30.0) as client:
            for start in range(0, n, EMBED_BATCH):
                chunk = _safe_inputs(texts[start : start + EMBED_BATCH])
                payload = {"model": self.model, "input": chunk}
                for attempt in range(MAX_RETRIES):
                    resp = client.post(self._url, json=payload, headers=self._headers)
                    if resp.status_code == 429:
                        time.sleep(min(60, 5 * (attempt + 1)))
                        continue
                    _check(resp, "AIHubMix")
                    data = sorted(resp.json()["data"], key=lambda d: d["index"])
                    out.extend(d["embedding"] for d in data)
                    break
                else:
                    raise RuntimeError(
                        f"AIHubMix embeddings: still rate-limited after {MAX_RETRIES} retries "
                        f"at batch {start} — {resp.text[:300]}"
                    )
                if progress:
                    print(f"      embedded {min(start + EMBED_BATCH, n)}/{n}", flush=True)
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, progress=True)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


class OpenRouterEmbedder:
    """OpenRouter hosted embeddings (OpenAI-compatible /embeddings endpoint,
    fixed public URL). Reuses openrouter_api_key, the same key already used
    for chat completions. The default free model (liquid/lfm-2.5-embedding-
    350m:free) HARD-ERRORS (HTTP 400, confirmed live — not a silent
    truncation) past 512 tokens per input, small next to a code symbol's full
    body — every text is truncated to a conservative char budget before
    sending, which does mean long symbols get embedded on their opening
    portion only, not their full body."""

    _MAX_CHARS = 1800  # ~512 tokens at a conservative ~3.5 chars/token

    def __init__(self) -> None:
        if not settings.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set in .env")
        self.model = settings.openrouter_embedding_model
        self.dim = settings.openrouter_embedding_dim
        self._url = "https://openrouter.ai/api/v1/embeddings"
        self._headers = {"Authorization": f"Bearer {settings.openrouter_api_key}"}

    def _embed(self, texts: list[str], progress: bool = False) -> list[list[float]]:
        out: list[list[float]] = []
        n = len(texts)
        with httpx.Client(timeout=30.0) as client:
            for start in range(0, n, EMBED_BATCH):
                batch = texts[start : start + EMBED_BATCH]
                chunk = _safe_inputs([t[: self._MAX_CHARS] for t in batch])
                payload = {"model": self.model, "input": chunk}
                for attempt in range(MAX_RETRIES):
                    resp = client.post(self._url, json=payload, headers=self._headers)
                    if resp.status_code == 429:
                        time.sleep(min(60, 5 * (attempt + 1)))
                        continue
                    _check(resp, "OpenRouter")
                    data = sorted(resp.json()["data"], key=lambda d: d["index"])
                    out.extend(d["embedding"] for d in data)
                    break
                else:
                    # Worth reading in full: the free tier's cap is per *day*, so
                    # this is not something the retry loop can ever wait out.
                    raise RuntimeError(
                        f"OpenRouter embeddings: still rate-limited after {MAX_RETRIES} retries "
                        f"at batch {start} — {resp.text[:300]}"
                    )
                if progress:
                    print(f"      embedded {min(start + EMBED_BATCH, n)}/{n}", flush=True)
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, progress=True)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


def get_embedder():
    """Return the active embedder, or None if configured provider is unavailable."""
    provider = settings.embedding_provider.lower()
    if provider == "local":
        return LocalEmbedder()
    if provider == "voyage":
        return VoyageEmbedder() if settings.voyage_api_key else None
    if provider == "alibaba":
        return AlibabaEmbedder() if settings.alibaba_api_key and settings.alibaba_base_url else None
    if provider == "aihubmix":
        return AihubmixEmbedder() if settings.aihubmix_api_key else None
    if provider == "openrouter":
        return OpenRouterEmbedder() if settings.openrouter_api_key else None
    return None
