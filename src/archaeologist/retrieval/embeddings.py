"""Embeddings for semantic search — pluggable by `settings.embedding_provider`.

- "local"   : fastembed (ONNX, e.g. BGE-small). Offline, no API key, no rate
              limits — but loads a real model into THIS process's memory,
              which is tight on a free-tier (512MB) deploy.
- "voyage"  : Voyage AI (code-tuned). Hosted; free tier is heavily rate-limited.
- "alibaba" : Alibaba Cloud Model Studio, hosted, OpenAI-compatible endpoint —
              reuses the same account already paying for the LLM. No model
              loaded in-process, so it's the one that actually helps the
              memory-constrained deploy case.

Every embedder exposes `.dim`, `.embed_documents(texts)`, and `.embed_query(text)`,
so the indexer and retriever don't care which one is active.
"""

import time

import httpx

from archaeologist.config import settings

EMBED_BATCH = 32
MAX_RETRIES = 8


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
                chunk = texts[start : start + self._MAX_PER_CALL]
                payload = {"model": self.model, "input": chunk, "dimensions": self.dim}
                for attempt in range(MAX_RETRIES):
                    resp = client.post(self._url, json=payload, headers=self._headers)
                    if resp.status_code == 429:
                        time.sleep(min(60, 5 * (attempt + 1)))
                        continue
                    resp.raise_for_status()
                    data = sorted(resp.json()["data"], key=lambda d: d["index"])
                    out.extend(d["embedding"] for d in data)
                    break
                else:
                    raise RuntimeError(f"Alibaba embeddings: exhausted retries at batch {start}")
                if progress:
                    print(f"      embedded {min(start + self._MAX_PER_CALL, n)}/{n}", flush=True)
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
    return None
