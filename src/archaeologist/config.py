"""Central configuration, loaded from environment / .env via pydantic-settings.

Import the singleton `settings` anywhere:

    from archaeologist.config import settings
    print(settings.postgres_url)
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- App ---
    app_env: str = Field(default="development")

    # --- LLM (reasoning / agent) — pluggable provider ---
    # "auto" (default): use a hosted key if one is set, else a local Ollama model
    # (no API key, no cost), else run in offline retrieval-only mode. Explicit
    # choices: "gemini" | "anthropic" | "openrouter" | "alibaba" | "ollama".
    llm_provider: str = Field(default="auto")
    gemini_api_key: str = Field(default="")
    gemini_model: str = Field(default="gemini-2.5-flash")
    anthropic_api_key: str = Field(default="")
    claude_model: str = Field(default="claude-sonnet-5")
    # OpenRouter — one key, many models; ":free"-suffixed models cost nothing
    # (rate-limited, e.g. 50 req/day on a free OpenRouter account). Good for a
    # pilot before committing to a paid, higher-limit provider.
    openrouter_api_key: str = Field(default="")
    openrouter_model: str = Field(default="openai/gpt-oss-20b:free")
    # Alibaba Cloud Model Studio (Qwen) — OpenAI-compatible endpoint, but the
    # host is workspace-specific (shown once when the key is created), not a
    # shared domain — must be set per account, no sensible global default.
    alibaba_api_key: str = Field(default="")
    alibaba_base_url: str = Field(default="")
    alibaba_model: str = Field(default="qwen-flash")
    # Local, API-key-free LLM served by Ollama (docker compose up -d; the first
    # boot pulls the model, ~5 GB). Any OpenAI-style endpoint also works.
    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="qwen2.5:7b")

    # --- Embeddings (provider decided in Phase 3; Anthropic has no embeddings API) ---
    # "local" = fastembed (offline, no key); "voyage" = hosted, code-tuned.
    embedding_provider: str = Field(default="local")
    local_embedding_model: str = Field(default="BAAI/bge-small-en-v1.5")
    local_embedding_dim: int = Field(default=384)
    voyage_api_key: str = Field(default="")
    voyage_model: str = Field(default="voyage-code-3")
    voyage_dim: int = Field(default=1024)
    # Seconds to wait between embedding batches. Free tier (no payment method) is
    # 3 RPM / 10K TPM — set ~42. With a payment method, leave 0 (standard limits).
    voyage_request_delay: float = Field(default=0.0)

    # --- Postgres ---
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5433)
    postgres_user: str = Field(default="archaeologist")
    postgres_password: str = Field(default="archaeologist")
    postgres_db: str = Field(default="archaeologist")

    # --- OpenSearch ---
    opensearch_host: str = Field(default="localhost")
    opensearch_port: int = Field(default=9200)
    opensearch_use_ssl: bool = Field(default=False)

    # --- Redis ---
    redis_host: str = Field(default="localhost")
    redis_port: int = Field(default=6379)

    # --- Observability (Phase 7) — Langfuse is optional; local telemetry always on ---
    langfuse_public_key: str = Field(default="")
    langfuse_secret_key: str = Field(default="")
    langfuse_host: str = Field(default="https://cloud.langfuse.com")

    # --- Ingestion (Phase 1) ---
    # The repository the archaeologist investigates.
    target_repo_url: str = Field(default="https://github.com/pallets/flask")
    # Where clones live on disk (relative to repo root; gitignored).
    repos_dir: str = Field(default="repos")
    # Read-only GitHub PAT; lifts the 60 req/hr unauthenticated limit for issues/PRs.
    github_token: str = Field(default="")

    @property
    def embedding_dim(self) -> int:
        """Vector dimension of the active provider — keeps index mappings correct
        even in BM25-only (no-embeddings) mode."""
        if self.embedding_provider.lower() == "voyage":
            return self.voyage_dim
        return self.local_embedding_dim

    @property
    def postgres_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def opensearch_url(self) -> str:
        scheme = "https" if self.opensearch_use_ssl else "http"
        return f"{scheme}://{self.opensearch_host}:{self.opensearch_port}"

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/0"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
