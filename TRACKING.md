# Multi-user migration — tracking

Working log for the "genuine multi-user product" migration (GitHub OAuth,
per-user private workspaces, operator-metered LLM/embedding budget). The
full design lives in the Claude Code plan file from the planning session;
this document tracks **what's actually been built, verified, and is still
pending** so work can resume across sessions without re-deriving context.

Do not put real secret values in this file — env var *names* only. `.env`
is gitignored; this file is not.

## Status

| Phase | What | Status |
|---|---|---|
| 1 | Auth foundation (GitHub OAuth + sessions) | **Done, verified except real GitHub round-trip** |
| 2 | Per-user repo ownership + IDOR fixes | **Done, verified** |
| 3 | OpenSearch isolation | **Done, verified — scope expanded beyond the plan, see below** |
| 4 | Per-user integration credentials (Confluence/Jira) | Not started |
| 5 | Shared, metered LLM/embedding budget | Not started |

---

## Phase 1 — Auth foundation

**Built:**
- `src/archaeologist/models/entities.py` — new `User` table (`github_id`, `github_login`, `email`, `avatar_url`, `created_at`). No FK from anything else yet — that's Phase 2.
- `src/archaeologist/auth.py` (new) — `get_current_user(request)` dependency, reads `request.session["user_id"]`, 401s if absent/stale.
- `src/archaeologist/routers/auth.py` (new) — `GET /api/auth/github/login`, `GET /api/auth/github/callback`, `POST /api/auth/logout`, `GET /api/auth/me`.
- `src/archaeologist/main.py` — `SessionMiddleware` (itsdangerous-signed cookie) added before `CORSMiddleware`; `allow_credentials=True` added to CORS; `auth.router` registered.
- `src/archaeologist/config.py` — new settings: `frontend_base_url`, `github_oauth_client_id`, `github_oauth_client_secret`, `session_secret`.
- `pyproject.toml` — added `itsdangerous>=2.2` as an **explicit** dependency. (The plan assumed it ships free with FastAPI/Starlette — confirmed live that's wrong; `SessionMiddleware` needs it but doesn't pull it in.)
- Frontend: `frontend/src/lib/api.ts` (`credentials: "include"` on every fetch, `api.me()`/`api.logout()`), `frontend/src/lib/AuthContext.tsx` (new), `frontend/src/lib/types.ts` (`User` type), `frontend/src/pages/Login.tsx` (new), `frontend/src/components/RequireAuth.tsx` (new, wraps all routes in `App.tsx` except `/login`), `frontend/src/main.tsx` (`AuthProvider` wired in), `frontend/src/components/Shell.tsx` (user chip + sign-out button in the sidebar footer).

**Verified (without needing a real GitHub account):**
- `users` table creation + upsert-by-`github_id` logic, tested directly against the real local Postgres.
- `GET /api/auth/me` → 401 when logged out.
- `GET /api/auth/github/login` → correctly builds the GitHub authorize URL (client_id, redirect_uri, scope, signed `state` in the session cookie) when configured; returns a clear 500 (not a crash) when `GITHUB_OAUTH_CLIENT_ID` is unset.
- Frontend: `tsc --noEmit` clean, Vite HMR applied all changes with no errors.

**Pending — needs you, not code:**
1. Register a GitHub OAuth App at github.com/settings/developers → OAuth Apps → New OAuth App.
   - Callback URL (local dev): `http://localhost:8000/api/auth/github/callback`
   - You'll need a **second** OAuth App (or update the callback) for production, pointing at the deployed domain.
2. Put the resulting values in `.env`:
   - `GITHUB_OAUTH_CLIENT_ID`
   - `GITHUB_OAUTH_CLIENT_SECRET`
   - `SESSION_SECRET` — already generated and set locally; **generate a separate one for production**, don't reuse the dev value.
3. Click through the real login flow once in a browser to confirm the full round-trip (GitHub's consent screen can't be tested from here).

---

## Phase 2 — Per-user repo ownership + IDOR fixes

**Schema:**
- `Repo` gains `user_id` FK; unique constraint moved from `url unique=True` to `UniqueConstraint(user_id, url)`.
- `IngestJob` gains `user_id` FK (it has no `repo_id` — a Repo row doesn't exist yet when a job starts).
- Local dev Postgres + both OpenSearch indices (`code_symbols`, `evidence`) were **dropped and recreated from scratch** — there's no Alembic/migration tool in this project (confirmed), and the existing local rows (OpenHarness/axon/flask/CoderLens test ingests) had no owner concept since auth didn't exist yet. Disposable test data; re-ingest as needed. **This does not apply to any hosted/production database** — nothing there has been touched.

**Backend — every route rewritten:**
- `src/archaeologist/routers/api.py` — `_repo(session, user)` now filters by `Repo.user_id`; every route takes `user: User = CurrentUser` and resolves data scoped to them. New `_owns_repo(session, user, repo_id)` helper for routes that take a raw id instead of resolving through `_repo()`.
- **The IDOR fixes** (previously zero ownership check at all): `GET /api/symbol/{id}`, `GET /api/callgraph/{id}` — now verify the symbol's repo is owned by the caller. `GET /api/impact/{id}` — now verifies the requested symbol actually belongs to the resolved active repo (it resolved a repo but never checked). `GET`/`DELETE /api/conversations/{id}` — ownership now enforced inside `services/conversations.py`'s `get_conversation`/`delete_conversation` (joined through `Conversation.repo_id -> Repo.user_id`), not just at the route.
- `src/archaeologist/routers/codemap.py` — `explain-edge`/`extend`/`simulate` now verify every symbol/node id they're handed belongs to the caller (`_check_owned` helper) before doing anything with it — previously zero check. Its two independently-written repo-pickers (`order_by(Repo.id.desc())`, ignoring `ingested_at`, could disagree with `api.py`'s `_repo()` mid-refresh) are gone — both files now share `api.py`'s `_repo()`/`_owns_repo()` via a direct cross-module import.
- `src/archaeologist/ingestion/pipeline.py` — `ingest_repository()`/`_full_ingest()`/`_refresh_only_issues()` now require `user_id` (keyword-only, no default — a CLI/notebook caller must pass a real one). **Critical fix beyond what the plan named explicitly**: the `Repo` lookup/creation in `_full_ingest` is scoped by `(url, user_id)`, not `url` alone — since `Repo.url` is no longer globally unique, an unscoped lookup here would find (and silently overwrite) a *different* user's repo row that happens to share the URL.
- `src/archaeologist/services/ingest.py` — `start_ingest`/`running_job_for` take `user_id`; `_serialize()` includes it so routes can check ownership; the post-ingest `Repo` re-fetch is scoped by `(url, user_id)` for the same reason as above (otherwise a later pipeline step could attach to the wrong user's repo).
- `src/archaeologist/services/conversations.py` — `get_conversation`/`delete_conversation` now require `user_id` and enforce it via a join, not just take a bare PK.
- New `POST /api/repos/{repo_id}/activate` — lets a user switch which of their already-ingested repos is "active" by bumping `ingested_at` to now (no re-ingest), since `_repo()` always resolves to the most-recently-ingested row. This is the whole mechanism behind the frontend switcher — avoided a much larger refactor of threading an explicit `repo_id` through every single page's API calls.

**Frontend:**
- `frontend/src/components/Shell.tsx` — a real repo switcher (`<select>` over `GET /api/repos`, shown only when the user has more than one repo) calling the new activate endpoint, then `queryClient.clear()` — every other query in the app (wiki, overview, graph, ...) has no `repo_id` of its own and just trusts the backend's "most recent" pick, so switching repos has to invalidate the whole cache, not just `["repo"]`.
- `frontend/src/lib/api.ts` — `activateRepo(repoId)`.

**Verified:**
- Full direct-database test: two synthetic users, same URL — confirmed independent `Repo`/`Symbol`/`Conversation` rows, `_repo()` resolves each user to their own, `_owns_repo()` correctly allows/denies, both symbol and conversation IDOR fixes block the non-owner and allow the owner, `delete_conversation` refuses to delete another user's row (and doesn't).
- HTTP-level test via FastAPI's `dependency_overrides`: two users each see only their own repo through `/api/repo` and `/api/repos`.
- Full test suite: 101 passed (up from 98 — fixed 3 real regressions in `tests/test_api.py`, which pre-dated auth and needed a `dependency_overrides`-injected fake user to keep testing the same validation logic). Same 7 pre-existing, environment-dependent failures as before Phase 2 (stale product-name assertion in `test_health.py`; `test_offline.py` assumes no LLM configured, but one is configured in this dev environment) — unrelated to this migration.

**Explicitly NOT done in this phase (by design, matches the plan):**
- `/api/search` and `/api/ask`/`/api/investigate` require login now but are **not yet repo-scoped at the OpenSearch layer** — that's Phase 3. Noted inline in the code (`search()`'s docstring/comment) so it isn't mistaken for already-fixed.
- No explicit `repo_id` query param threaded through individual pages (Tour, Explorer, Graph, ...) — the activate-endpoint approach above covers the actual user-facing need (switch repos) without that larger refactor. Revisit only if a real need for "view two repos side by side" or deep-linking to a specific repo emerges.

**A pre-existing behavior worth knowing (not a Phase 2 bug, but relevant to it):** `clone_or_open()` only clones once — if the same URL's clone directory already exists on disk it just reopens it, with no fetch. So if two different users both ingest the same public URL, the second one's `head_sha` may reflect whatever the first user's clone happened to be at, not truly latest-upstream. This is the same "refresh doesn't actually re-fetch" limitation flagged earlier this session, just more visible now that a shared clone can serve multiple users' `Repo` rows.

---

## Phase 3 — OpenSearch isolation

**Scope grew significantly beyond the plan's framing of "add a `repo_id`
filter to queries."** Investigating that turned up two more severe,
pre-existing bugs — both fixed here, not deferred:

1. **Every ingest wiped every other repo's OpenSearch data.**
   `code_index.create_index()`/`evidence_index.create_index()` were called
   with `recreate=True` **unconditionally** on every single ingest
   (`indexing/run.py`, `indexing/streams_run.py`), and `create_index`'s own
   logic is "if the index exists and `recreate=True`, delete the whole index,
   then recreate it empty." So indexing repo B would silently delete repo A's
   (and every other repo's) indexed code symbols and evidence docs — this
   predates multi-tenancy entirely; in the single-shared-workspace app,
   `/api/search`/`/api/ask`/`/api/investigate` effectively only ever worked
   correctly for whichever repo was ingested *most recently*. Postgres data
   for other repos was unaffected (wiki/dead-code/overview/etc. still worked),
   only the OpenSearch-backed features were silently broken for everyone else.
2. **Evidence doc IDs collided across repos even without the wipe bug.**
   `evidence_index.index_documents()` used `f"{stream}:{ref_id}"` as the
   OpenSearch `_id`. `ref_id` isn't globally unique — issue/PR numbers restart
   at 1 per repo, doc chunk ids are `f"{file_path}#{i}"` (e.g. "README.md#0")
   which repeats across repos. Two different repos' evidence documents could
   silently overwrite each other in the shared index, independent of bug #1.
3. **`build_codemap`/`extend_codemap` picked an arbitrary repo, not the
   caller's.** `/api/codemap` runs an unfiltered OpenSearch search over the
   free-text question, then derived `repo_id = ordered[0].repo_id` from
   whichever symbol happened to rank first — meaning a codemap could be built
   from a **different user's repo entirely**, purely based on which repo's
   symbols best matched the query. Worse than a missing filter: it could
   silently hand back another user's codebase structure as if it were the
   caller's own.

**Fixed:**
- `indexing/code_index.py`, `indexing/evidence_index.py`: `create_index()`
  now defaults to `recreate=False` and only destroys/rebuilds the index on an
  explicit request or a genuine embedding-dimension change (detected via
  `_existing_dim()`) — not on every routine ingest. New `delete_repo_docs(client, repo_id)`
  in both modules (via `delete_by_query`) — the additive, per-repo counterpart
  that replaces "wipe the whole index."
- `evidence_index.py`: added `repo_id` to the index mapping and to every doc
  dict built in `indexing/streams.py` (`_doc_files`/`_commits`/`_issues`).
  Fixed `index_documents()`'s `_id` scheme to `f"{repo_id}:{stream}:{ref_id}"`.
- `code_index.py`/`evidence_index.py`: `bm25_hits`/`knn_hits` (and `search`)
  now take a required `repo_id` and filter by it (`term` filter for BM25 and
  the evidence k-NN path; the Lucene engine's native k-NN `filter` for
  code's k-NN path, since `code_index` uses `engine: lucene`).
- `retrieval/multi.py::search_all()` gains a required `repo_id` param,
  threaded into all four hit-fetching calls.
- `indexing/run.py::index_to_opensearch()` / `indexing/streams_run.py::build_evidence_index()`:
  resolve the repo first, `create_index()` (now safe to call every time —
  it's a no-op unless something really changed), then `delete_repo_docs(repo.id)`
  before indexing that repo's fresh docs.
- `analysis/codemap.py`: `build_codemap()`/`extend_codemap()`/`_candidate_ids()`
  now take a required `repo_id` instead of deriving one from the top search
  hit. `routers/codemap.py` resolves it via `_repo(s, user)` (for `/codemap`)
  or from the already-ownership-checked `existing_ids`' repo (for `/codemap/extend`)
  before calling in.
- `retrieval/graph_queries.py::find_symbol()` now takes a required `repo_id`
  (previously matched by `qualified_name` alone, globally — the investigate
  agent's graph-expansion step could resolve a same-named symbol from a
  different repo). `agent/tools.py::search()`/`graph_expand()`,
  `agent/state.py`'s `InvestigationState`, `agent/nodes.py::retrieve_node()`,
  `agent/graph.py::investigate()`/`investigate_stream()` all thread `repo_id`
  through end to end.
- `rag/pipeline.py::retrieve()`/`answer_question()` take a required `repo_id`.
- `routers/api.py`: `/api/search`, `/api/ask`, `/api/investigate`,
  `/api/investigate/stream` now resolve `repo_id = _repo(s, user).id` and
  pass it all the way through — the "logged in but not actually repo-scoped"
  gap noted in Phase 2 is closed.
- CLI/eval entry points updated to match the new required `repo_id` params:
  `rag/ask.py` (new `--repo-id` flag, default most-recently-ingested),
  `eval/answer_eval.py`, `eval/answer_run.py`, `eval/localization.py`,
  `eval/run.py`, `retrieval/hybrid.py::hybrid_search()`, and the `--query`
  demo paths in `indexing/run.py`/`indexing/streams_run.py`.

**Verified:**
- Full test suite: 101 passed, same 7 pre-existing environment-dependent
  failures as before this phase (stale product-name assertion, offline-mode
  assumptions, one live 429 from an exhausted OpenRouter quota) — no
  regressions from this phase's changes.
- Direct OpenSearch-level test: two synthetic repos, distinct code symbols
  and an evidence doc sharing the same `stream`+`ref_id` (`issue:1` in both)
  — confirmed `search_all(repo_id=A)` and `search_all(repo_id=B)` each see
  only their own repo's hits, and confirmed the two `issue:1` documents are
  stored as two distinct OpenSearch documents (`{A}:issue:1`, `{B}:issue:1`),
  not one overwriting the other.
- Direct OpenSearch-level test: indexed repo A and repo B, then re-ran the
  exact call sequence `indexing/run.py` now uses to re-ingest repo A alone
  (`create_index()` → `delete_repo_docs(A)` → index A's new docs) — confirmed
  repo B's previously-indexed documents were untouched afterward.

**Explicitly NOT done in this phase:**
- `routers/ask.py` (an old, unregistered `/ask` router — superseded by
  `/api/ask` in `routers/api.py`, confirmed not imported/mounted anywhere)
  still calls `answer_question()` with the old signature and would fail if
  ever wired up. Left as-is since it's genuinely dead code; flagging here so
  it isn't mistaken for a live gap.
- `notebooks/08_phase8_simulation.ipynb` calls `build_codemap(QUESTION)`
  with the old signature — will need a `repo_id` argument added the next
  time that notebook is actually re-run (notebooks are point-in-time
  validation snapshots, not live app code, per the project's own convention).

---

## Deviations from the original plan (so future-you isn't confused by a diff)

- `itsdangerous` added as an explicit `pyproject.toml` dependency — the plan said it was free; it isn't.
- Frontend repo-switching uses a new "activate" endpoint (bump `ingested_at`) rather than threading an explicit `repo_id` through every page as the plan originally described — same user-facing outcome, much smaller diff.
- Phase 3's plan described "add a `repo_id` filter to queries" as roughly the whole job. It wasn't — see the Phase 3 section above for the two additional pre-existing bugs (destructive unconditional index recreation on every ingest, and cross-repo evidence-doc id collisions) that had to be fixed alongside the filtering, plus the codemap-feature repo-derivation bug found while auditing every OpenSearch call site.
