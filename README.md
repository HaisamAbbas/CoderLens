# CoderLens

An AI engineer that investigates an unfamiliar production codebase and answers
**"why does this work the way it does?"** — reconstructing evolution, dependencies,
decisions, and failure modes, with **evidence-cited** answers (`path:line`, commit SHA, issue #).

Modeled on the architecture and paradigm of the
[production-agentic-rag-course](https://github.com/jamwithai/production-agentic-rag-course)
(arxiv-paper-curator), applied to the much harder domain of a **Git repository** instead of arXiv PDFs.

## Why it's harder than PDF RAG

A paper is one flat text stream. A codebase is **five correlated streams** joined by shared keys
(file path, symbol, commit SHA, line range):

| Stream | Content |
|---|---|
| Code | tree-sitter AST → functions / classes / methods / imports / endpoints |
| Docs | README, ADRs, docstrings, comments |
| Git history | commits, diffs, authors, timestamps |
| Issues / PRs | discussion, review, resolution |
| Dependency graph | who-calls-whom / who-imports-whom |

Chunk **by symbol** (not fixed token windows). Postgres holds symbols + graph edges + join keys;
OpenSearch holds BM25 + embeddings (RRF fusion) per stream. A LangGraph **investigation agent**
routes questions across streams, grades evidence sufficiency, and loops until it can cite sources.

## Stack

- **Reasoning / agent:** Claude API · **Retrieval:** OpenSearch 2.19 (BM25 + vectors + RRF)
- **Metadata + symbol graph:** PostgreSQL 16 · **Cache:** Redis · **API:** FastAPI · **Packaging:** uv
- **First target of investigation:** [Flask](https://github.com/pallets/flask)

## Roadmap

| Phase | Goal |
|---|---|
| 0 | Infra scaffold (Docker stack, FastAPI, config) — **current** |
| 1 | Ingestion: clone Flask, walk 5 streams into Postgres |
| 2 | Code-aware indexing: tree-sitter symbols + BM25 |
| 3 | Hybrid retrieval: embeddings + BM25 + RRF |
| 4 | Multi-index + dependency graph |
| 5 | RAG with evidence citations |
| 6 | LangGraph investigation agent |
| 7 | SWE-bench eval + Langfuse observability |

## No API key required

Users don't need to bring their own LLM key. Paste a repository URL and go:

- **`docker compose up -d`** starts Postgres, OpenSearch, Redis — and **Ollama**, a
  local, API-key-free LLM (first boot downloads the model, ~5 GB, once).
- Provider resolution is automatic (`LLM_PROVIDER=auto`): a hosted key
  (`GEMINI_API_KEY` / `ANTHROPIC_API_KEY`) wins if set; otherwise the local
  Ollama model is used; otherwise the app runs in **offline mode** — retrieval,
  the graph, the tour, and codemaps all still work, and /ask + /investigate
  return the evidence itself as a cited digest.
- Embeddings are local too (fastembed ONNX by default) — no API calls anywhere.

The status endpoint (`GET /api/status`) reports which LLM/embedding provider is
active, and the sidebar shows it.

## Quickstart (Phase 0)

```bash
cp .env.example .env        # add your ANTHROPIC_API_KEY
uv sync --extra dev         # create venv + install deps (incl. ipykernel)
docker compose up -d        # Postgres + OpenSearch + Redis
uv run uvicorn archaeologist.main:app --reload
```

**Validate the stack:** open [notebooks/00_phase0_infrastructure.ipynb](notebooks/00_phase0_infrastructure.ipynb)
in VS Code, select the `.venv` kernel, and **Run All**. It checks Postgres, OpenSearch, Redis,
the Claude API, and the FastAPI app, printing a PASS/FAIL summary.

Or check the API directly:
- http://localhost:8000/docs — API docs
- http://localhost:8000/health — liveness
- http://localhost:8000/health/deps — Postgres / OpenSearch / Redis connectivity

## Notebooks

Following the reference course, every phase ships a `notebooks/NN_phaseN_*.ipynb` that validates and
demonstrates that phase's services before/while the real code lands in `src/`. `src/` is the app;
`notebooks/` proves it works.
