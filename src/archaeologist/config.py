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
    # Extra CORS origins beyond the built-in Vite dev-server ones (comma-separated,
    # e.g. "https://coderlens.onrender.com") — only needed if the frontend is ever
    # hosted separately from this API; the SPA-fallback path in main.py serving
    # frontend/dist from the same origin needs none of this.
    cors_origins: str = Field(default="")
    # Absolute path to the built frontend (frontend/dist). Blank means "derive
    # it from this file's location, assuming we're running from the source
    # checkout" — true for local `uv run`, but NOT once the package is
    # `pip install`-ed (main.py then lives under site-packages, nowhere near
    # the actual frontend/ directory). A real deployment must set this.
    frontend_dist: str = Field(default="")

    # --- LLM (reasoning / agent) — pluggable provider ---
    # "auto" (default): use a hosted key if one is set, else a local Ollama model
    # (no API key, no cost), else run in offline retrieval-only mode. Explicit
    # choices: "gemini" | "anthropic" | "openrouter" | "alibaba" | "aihubmix" | "ollama".
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
    # AIHubMix — OpenAI-compatible aggregator, one key routes to many hosted
    # models (GLM, Qwen, OpenAI, Claude, ...) via a single FIXED public
    # endpoint (unlike Alibaba's workspace-specific URL, so no base-url setting
    # is needed here).
    aihubmix_api_key: str = Field(default="")
    # minimax-m3-free — confirmed live: genuinely free (input/output/cache_read
    # all $0 in AIHubMix's own catalog), 1M+ context, reasoning + long-context.
    aihubmix_model: str = Field(default="minimax-m3-free")
    # Local, API-key-free LLM served by Ollama (docker compose up -d; the first
    # boot pulls the model, ~5 GB). Any OpenAI-style endpoint also works.
    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="qwen2.5:7b")

    # --- Embeddings (provider decided in Phase 3; Anthropic has no embeddings API) ---
    # "local" = fastembed (offline, no key, but loads a real ONNX model into
    # this process's memory — tight on a free-tier 512MB deploy); "voyage" =
    # hosted, code-tuned, but free tier is brutally rate-limited; "alibaba" =
    # hosted via the same Model Studio account already used for the LLM, no
    # model loaded in-process. Reuses alibaba_api_key/alibaba_base_url above.
    embedding_provider: str = Field(default="local")
    local_embedding_model: str = Field(default="BAAI/bge-small-en-v1.5")
    local_embedding_dim: int = Field(default=384)
    voyage_api_key: str = Field(default="")
    voyage_model: str = Field(default="voyage-code-3")
    voyage_dim: int = Field(default=1024)
    # text-embedding-v2 is access-denied on at least some workspaces (confirmed
    # live); v3/v4 both work and default to 1024 dims (Matryoshka-configurable).
    alibaba_embedding_model: str = Field(default="text-embedding-v3")
    alibaba_embedding_dim: int = Field(default=1024)
    # AIHubMix, code-tuned (jina-embeddings-v2-base-code) — reuses aihubmix_api_key.
    # Dim verified live below, not guessed (AIHubMix's own docs explicitly warn
    # not to assume a model's parameters without a real call).
    aihubmix_embedding_model: str = Field(default="jina-embeddings-v2-base-code")
    aihubmix_embedding_dim: int = Field(default=768)
    # OpenRouter — genuinely free (confirmed live), reuses openrouter_api_key.
    # 1024-dim confirmed live. Hard-errors (400, not silent truncation) past
    # 512 tokens/input — every text is truncated before sending, see
    # OpenRouterEmbedder in retrieval/embeddings.py.
    openrouter_embedding_model: str = Field(default="liquid/lfm-2.5-embedding-350m:free")
    openrouter_embedding_dim: int = Field(default=1024)
    # Seconds to wait between embedding batches. Free tier (no payment method) is
    # 3 RPM / 10K TPM — set ~42. With a payment method, leave 0 (standard limits).
    voyage_request_delay: float = Field(default=0.0)

    # --- Postgres ---
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5433)
    postgres_user: str = Field(default="archaeologist")
    postgres_password: str = Field(default="archaeologist")
    postgres_db: str = Field(default="archaeologist")
    # Hosted Postgres (Neon, Supabase, ...) requires TLS; the local Docker
    # instance doesn't, so this stays blank/off by default.
    postgres_sslmode: str = Field(default="")

    # --- OpenSearch ---
    opensearch_host: str = Field(default="localhost")
    opensearch_port: int = Field(default=9200)
    opensearch_use_ssl: bool = Field(default=False)
    # Hosted OpenSearch (e.g. Bonsai's free tier) requires HTTP basic auth;
    # the local Docker instance doesn't, so both stay optional/blank by default.
    opensearch_user: str = Field(default="")
    opensearch_password: str = Field(default="")

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

    # --- Confluence Cloud publish (Phase 8) ---
    # e.g. https://your-domain.atlassian.net/wiki (must include the /wiki path)
    confluence_base_url: str = Field(default="")
    confluence_email: str = Field(default="")          # account owning the API token
    confluence_api_token: str = Field(default="")       # id.atlassian.com/manage-profile/security/api-tokens
    confluence_space_key: str = Field(default="")       # destination space; one global space for now
    # mermaid.ink PNG rendering vs. always-fallback-to-code-macro
    confluence_render_diagrams: bool = Field(default=True)
    confluence_mermaid_ink_url: str = Field(default="https://mermaid.ink")

    # --- Jira Cloud tickets (Phase 8) ---
    # Independent from the Confluence fields on purpose: same Atlassian account
    # usually works, but neither feature may silently fall back to the other's
    # credentials.
    jira_base_url: str = Field(default="")        # e.g. https://your-domain.atlassian.net (no /wiki path)
    jira_email: str = Field(default="")            # same Atlassian account as Confluence usually works
    jira_api_token: str = Field(default="")        # id.atlassian.com/manage-profile/security/api-tokens
    jira_project_key: str = Field(default="")
    jira_issue_type: str = Field(default="Task")   # "Task" exists in every project template; "Bug" doesn't always

    @property
    def confluence_configured(self) -> bool:
        return bool(self.confluence_base_url and self.confluence_email
                    and self.confluence_api_token and self.confluence_space_key)

    @property
    def jira_configured(self) -> bool:
        return bool(self.jira_base_url and self.jira_email
                    and self.jira_api_token and self.jira_project_key)

    @property
    def embedding_dim(self) -> int:
        """Vector dimension of the active provider — keeps index mappings correct
        even in BM25-only (no-embeddings) mode."""
        provider = self.embedding_provider.lower()
        if provider == "voyage":
            return self.voyage_dim
        if provider == "alibaba":
            return self.alibaba_embedding_dim
        if provider == "aihubmix":
            return self.aihubmix_embedding_dim
        if provider == "openrouter":
            return self.openrouter_embedding_dim
        return self.local_embedding_dim

    @property
    def postgres_url(self) -> str:
        url = (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )
        return f"{url}?sslmode={self.postgres_sslmode}" if self.postgres_sslmode else url

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
