# Multi-user migration — tracking

Working log for the "genuine multi-user product" migration (GitHub OAuth,
per-user private workspaces, per-user LLM usage tracking for operator
visibility — see Phase 5 below for why this isn't a budget/cap). The
full design lives in the Claude Code plan file from the planning session;
this document tracks **what's actually been built, verified, and is still
pending** so work can resume across sessions without re-deriving context.

Do not put real secret values in this file — env var *names* only. `.env`
is gitignored; this file is not.

## Status

| Phase | What | Status |
|---|---|---|
| 1 | Auth foundation (GitHub OAuth + sessions) | **Done, verified — real GitHub round-trip confirmed** |
| 2 | Per-user repo ownership + IDOR fixes | **Done, verified** |
| 3 | OpenSearch isolation | **Done, verified — scope expanded beyond the plan, see below** |
| 4 | Per-user integration credentials (Confluence/Jira) | **Done, verified except a real Confluence/Jira round-trip** |
| 5 | LLM usage tracking (rescoped — see below) | **Done, verified** |
| 6 | Guest access (browse public repos without login) | **Done, verified — not in the original plan, see below** |
| 6b | Saved GitHub PAT (per-user, for private-repo ingest) | **Done, verified — not in the original plan, see below** |

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
- `GET /api/auth/me` → 401 when logged out. **(Superseded by Phase 6:** it now
  auto-resolves to a guest account instead of 401ing — see below.)
- `GET /api/auth/github/login` → correctly builds the GitHub authorize URL (client_id, redirect_uri, scope, signed `state` in the session cookie) when configured; returns a clear 500 (not a crash) when `GITHUB_OAUTH_CLIENT_ID` is unset.
- Frontend: `tsc --noEmit` clean, Vite HMR applied all changes with no errors.

**Real GitHub round-trip: done.** A GitHub OAuth App was registered
(callback `http://localhost:8000/api/auth/github/callback`), its client
ID/secret added to `.env`, and the actual browser login flow completed
successfully.

That surfaced one real bug along the way, worth knowing about since it'll
resurface if the dev proxy setup ever changes: `frontend/vite.config.ts`'s
`/api` proxy didn't set `changeOrigin: true`, so a browser request routed
through the Vite dev server (`:5173`) reached the backend with
`Host: localhost:5173` still attached — and `routers/auth.py`'s
`_callback_url()` builds the OAuth redirect URL from that incoming request's
Host header on purpose (so the same code works in dev and prod with no
extra setting). The mismatch made GitHub reject the login with "redirect_uri
is not associated with this application," even though the client ID and the
registered callback URL matched exactly — the actual redirect_uri being
sent silently didn't. Fixed by setting `changeOrigin: true` **and** pointing
the proxy target at `localhost` rather than `127.0.0.1` (changeOrigin copies
the target's host into that header verbatim, and it has to match the
registered callback string-for-string — `127.0.0.1:8000` and
`localhost:8000` are two different strings as far as GitHub's exact-match
check is concerned, even though they're the same machine).

**Still needs you, not code, before a production deploy:**
1. A **second** GitHub OAuth App (or an updated callback URL on this one)
   pointing at the deployed domain — the one just registered is scoped to
   `localhost:8000`.
2. A **separate** `SESSION_SECRET` for production — don't reuse the local
   dev value.
3. Whatever host runs the built frontend needs the same `changeOrigin`
   consideration if it also proxies `/api` through something other than
   serving the FastAPI app directly (the prod SPA-fallback path in
   `main.py` doesn't proxy at all — frontend/dist is served from the same
   origin as the API — so this is a dev-only concern unless that changes).

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

## Phase 4 — Per-user integration credentials (Confluence/Jira)

Replaces the old global `CONFLUENCE_*`/`JIRA_*` env vars (one operator-
configured account shared by everyone) with per-user, encrypted-at-rest
credentials — this is the actual feature the "how would other users publish
their own docs" question earlier in the migration was asking for.

**Schema:**
- New `UserIntegration` table (`user_id` unique FK to `users`): confluence
  base_url/email/space_key + `confluence_api_token_encrypted`, jira
  base_url/email/project_key/issue_type + `jira_api_token_encrypted`.
  API tokens only — never the base_url/email/space_key/project_key, which
  aren't secret and are shown back to the user in Settings.

**Encryption:**
- New `src/archaeologist/security.py` — `encrypt()`/`decrypt()` via
  `cryptography`'s `Fernet` (AES + HMAC), keyed by a new
  `CREDENTIALS_ENCRYPTION_KEY` setting (one operator-held key, not
  per-user — the threat model is "don't leave tokens in plaintext in
  Postgres," not key-per-tenant isolation). New `cryptography>=42`
  dependency. A local dev key was generated and added to `.env` (gitignored);
  **production needs its own, separately generated** — do not reuse the dev
  value (same rule as `SESSION_SECRET` from Phase 1).

**Backend:**
- New `src/archaeologist/services/user_integrations.py` — `get()`,
  `confluence_configured()`/`jira_configured()`, `confluence_credentials()`/
  `jira_credentials()` (decrypted, ready to use), `upsert_confluence()`/
  `upsert_jira()` (a blank `api_token` on an update means "keep the existing
  one," never "clear it" — the frontend never has to see or re-send a saved
  token to change other fields), `clear_confluence()`/`clear_jira()`.
- `services/confluence_client.py` / `services/jira_client.py` — `open_client()`
  now takes `(base_url, email, api_token)` explicitly instead of reading
  global `settings`. Error messages updated to point at Settings instead of
  env var names.
- `services/confluence_publish.py::publish_wiki()` takes a `credentials`
  dict (from `user_integrations.confluence_credentials()`) instead of
  reading `settings.confluence_space_key`. `confluence_render_diagrams`/
  `confluence_mermaid_ink_url` stay global settings — they're operator-level
  rendering config, not a per-user credential.
- `services/confluence_job.py` / `services/jira_ticket.py` — resolve the
  owning user via `Repo.user_id` (already available from Phase 2 — no new
  `user_id` column needed on the job tables) and look up that user's
  credentials before publishing/ticketing; raise a clear error ("...set it
  up in Settings") if not connected.
- `routers/api.py`: `/api/confluence/publish` and `/api/jira/tickets` now
  400 with a clear message if the CALLER (not a global operator flag) hasn't
  connected the relevant integration. `/api/status`'s `confluence.configured`/
  `jira.configured` are now per-user — this required a new
  `get_current_user_optional`/`OptionalUser` dependency in `auth.py` (returns
  `None` instead of 401) since `/api/status`'s LLM/embedding info must stay
  reachable logged-out, but confluence/jira gating needed to become
  per-user without breaking that.
- New `routers/integrations.py` (`GET/PUT/DELETE /api/integrations/confluence`,
  same for `/jira`) — `GET` never returns a token, only a `has_token` boolean.

**Frontend:**
- New `frontend/src/pages/Settings.tsx` — two cards (Confluence, Jira), each
  with its own form, a "Connected"/"Not connected" badge, Save/Disconnect.
  Token field is `type="password"`, always starts blank, with a placeholder
  telling the user blank means "keep the current one." Saving invalidates
  both `["integrations"]` and `["status"]` so the Confluence/Jira buttons
  elsewhere in the app (Tour, Bug Hunter) update immediately.
- `Shell.tsx` gets a "Settings" nav entry (new `GearIcon`, sourced from the
  same Google Material Icons set as every other icon in the app).
- `lib/api.ts` gets a new `put()` helper (the app previously only had
  `get`/`post`/`del`) plus `integrations()`/`putConfluenceIntegration()`/
  `deleteConfluenceIntegration()`/`putJiraIntegration()`/`deleteJiraIntegration()`.

**Verified:**
- Full test suite: 101 passed, same 7 pre-existing unrelated failures.
  `tests/test_confluence.py`'s `publish_wiki` test updated for the new
  `credentials` parameter and `open_client(base_url, email, api_token)`
  signature.
- Direct test: `encrypt()`/`decrypt()` round-trip; two synthetic users each
  with their own Confluence/Jira credentials — confirmed user2 sees zero
  trace of user1's integration via `user_integrations.get()`; confirmed a
  blank `api_token` on a second `upsert_confluence()` call keeps the
  original token while other fields still update.
- HTTP-level test via `TestClient`/`dependency_overrides`: `GET /api/integrations`
  never includes `api_token` in the response (`has_token` boolean only);
  `PUT /api/integrations/confluence` then `GET` reflects the saved
  non-secret fields; `/api/status` correctly reports `confluence.configured`
  per-user (required overriding `get_current_user_optional`, not
  `get_current_user`, in the test — a reminder that `/api/status` uses a
  different dependency than every other route).
- `frontend`: `tsc --noEmit` clean.

**Explicitly NOT done in this phase:**
- No real Confluence/Jira account was connected end-to-end (no such account
  available in this environment) — the actual external API calls
  (`create_page`/`create_issue`/etc.) are unchanged from before this phase
  and were already covered by `test_confluence.py`'s `httpx.MockTransport`
  tests; only the credential *plumbing* (where they come from) changed here.
- Old `.env` values for `CONFLUENCE_*`/`JIRA_*` are now silently ignored
  (pydantic-settings `extra="ignore"`) rather than erroring — harmless, but
  worth knowing if a stale `.env` still has them.

---

## Phase 5 — LLM usage tracking (rescoped from the original plan)

**The original plan's Phase 5 was a per-user monthly $ cap** — enforced at
`call_llm`/`call_llm_stream`, with a hard mid-job stop once a user's own
usage hit their limit. Partway into planning it, explicit direction came
back: **LLM/embedding cost is the operator's to fund, not the client's —
clients should never be capped or blocked over cost.** Two follow-up
choices narrowed the actual scope: (1) track total spend for operator
visibility only, no caps/blocking of any kind, and (2) still attribute each
call to the user who triggered it (not just an aggregate total), so the
operator can see which user's activity costs what.

**What this phase actually built — a pure logging feature, nothing enforced:**

- New `UsageLedger` table (`user_id`, `kind` "llm"/"embedding", `provider`,
  `model`, `label`, `prompt_tokens`, `completion_tokens`, `estimated`,
  `cost_usd`, `created_at`).
- New `rag/pricing.py` — a static $/M-token table. **Only figures actually
  confirmed live this session are hardcoded** (zai's glm-5.3-flash
  $0.075/$0.25, aihubmix's glm-5.3-flash $0.11/$0.39, aihubmix's
  minimax-m3-free $0) — no guessed public list prices for gemini/anthropic/
  alibaba, since a wrong guess could quietly mislead the operator about real
  spend. Any unlisted/unverified provider+model falls back to the most
  expensive KNOWN paid price in the table, never `$0`.
- New `services/usage.py::record()` — called from `rag/llm.py`'s
  `call_llm`/`call_llm_stream` (both gained an optional `user_id` kwarg).
  Best-effort: wrapped in `try/except Exception: pass` so a logging failure
  can never break the LLM call it's attached to. `user_id=None` (CLI/eval
  callers with no signed-in user) silently skips recording — nothing to
  attribute the cost to.
- **Token counts are ESTIMATED from character length** (~3.5 chars/token,
  the same ratio `OpenRouterEmbedder` already uses), not each provider's
  real reported usage — every row is flagged `estimated=True`. Getting real
  per-provider counts would mean changing what all 7 `_call_X` functions in
  `rag/llm.py` return; not worth that surface area for a visibility-only
  feature with no number riding on it besides a query result.
- **`user_id` threaded through every real LLM call site** so the ledger's
  per-user attribution is actually accurate, not a guess: `agent/state.py`'s
  `InvestigationState` (alongside the `repo_id` Phase 3 already added),
  `agent/graph.py`'s `investigate()`/`investigate_stream()`,
  `rag/pipeline.py::answer_question()`, `analysis/codemap.py`'s
  `build_codemap`/`extend_codemap`/`explain_edge`/`_concept_cards`,
  `analysis/simulation.py::simulate_flow()`, `analysis/wiki.py`'s
  `build_wiki`/`_decide_structure`/`_write_prose` (including its
  `ThreadPoolExecutor` fan-out — passed as a plain argument, not a
  contextvar, for the same reason Phase 5's original plan already
  identified: a contextvar set on the parent thread isn't visible inside
  pool workers), `analysis/weaknesses.py`'s `scan_files`/`_scan_file` (same
  `ThreadPoolExecutor` situation), `analysis/snapshot.py::build_snapshot()`.
  Background jobs (`services/weakness_scan.py`) resolve `user_id` via
  `Repo.user_id` — already available, no new job-table column needed, same
  pattern Phase 4 used for Confluence/Jira job credentials.

**Explicitly NOT done (deliberate scope cuts, not oversights):**
- **No enforcement of any kind** — no `check_budget()`, no exception type, no
  mid-job stop, no changes to `weaknesses.py`/`wiki.py`'s submission loops
  beyond passing `user_id` through. This was the whole point of the rescope.
- **Embeddings are not tracked** — only `kind="llm"` rows exist today.
  Adding embedding tracking would mean touching all 5 embedder classes in
  `retrieval/embeddings.py` (each with its own `_embed`/`embed_documents`/
  `embed_query`) plus every `get_embedder()` call site, for comparatively
  little insight: the local embedder is free/in-process, and the paid
  embedders (alibaba/aihubmix/openrouter) are mostly used once per repo
  ingest, not per-request. Worth adding later if embedding cost turns out
  to matter; skipped here to keep this phase proportionate to "just track
  spend."
- **No dashboard or admin endpoint** — the ledger is queried directly for
  now, e.g.:
  ```sql
  SELECT user_id, SUM(cost_usd) AS total_cost, COUNT(*) AS calls
  FROM usage_ledger GROUP BY user_id ORDER BY total_cost DESC;
  ```
  A `/api/admin/usage` endpoint (operator-only) is a natural, small
  follow-up if this needs to be checked more than occasionally — not built
  here since it wasn't asked for.

**Verified:**
- Full test suite: 101 passed, same pre-existing environment-dependent
  failures as every prior phase. Fixed 3 real regressions in
  `tests/test_weaknesses.py` (three `_scan_file` monkeypatch stand-ins took
  only `f: File`, one positional arg — now `pool.submit(_scan_file, f, user_id)`
  passes two). Also fixed `tests/test_offline.py::_state()`, which was
  missing `repo_id` **and now `user_id`** in its hand-built `InvestigationState`
  fixture — this had actually been silently broken since Phase 3 added
  `repo_id` (4 of the "same 7 pre-existing failures" carried forward through
  Phases 3 and 4 were this exact bug, mislabeled as "pre-existing,
  unrelated" without being individually root-caused at the time. Caught and
  fixed now).
- Direct test: `pricing.price_per_million()`/`estimate_cost()` return the
  expected confirmed figures and correctly refuse to default an unlisted
  provider to `$0`; `usage.record()` end-to-end — two calls for one
  synthetic user produced two `UsageLedger` rows with `estimated=True` and
  real `cost_usd` values, and a call with `user_id=None` correctly created
  zero rows.
- `uv run python -c "import archaeologist.main"` clean (catches any
  signature-threading mistake across the ~13 call sites touched).

---

## Phase 6 — Guest access (browse public repos without login)

**Not in the original 5-phase plan** — added afterward: "for public repos it
should show front page [without requiring login]; if a user wants to ingest
a private repo, they can log in with GitHub." A real product decision was
needed before building it — the original Phase 5 plan already established
LLM cost as fully operator-funded with no per-user cap (see Phase 5 above),
so "should guests get LLM features too" was a genuine cost-exposure
question, not a detail. Answered explicitly: **yes, full parity** — a guest
gets the whole app, including Investigate/Ask/Bug Hunter/Codemap's AI
curation, identical to a signed-in user. Only two things are gated: (1)
ingesting a **private** repo (needs a real GitHub-linked account, since the
whole point of signing in there is accountability for a pasted PAT — the
token itself would work regardless of the app-account's own auth state, but
requiring real login matches the user's own mental model of "prove it's
your repo"), and (2) Confluence/Jira integrations (structurally impossible
for a guest — there's no durable identity to attach saved credentials to).

**The core design decision**: a guest gets a REAL `User` row
(`is_guest=True`, a synthetic negative `github_id` so it can never collide
with a real — always positive — GitHub account id), auto-created on first
use and remembered via the session cookie (`{"guest_user_id": int}`,
alongside — never together with — the existing `{"user_id": int}` for real
accounts). This means **every** per-user ownership/IDOR-safe mechanism built
in Phases 2 and 3 (`_repo()`, `_owns_repo()`, every OpenSearch `repo_id`
filter) works for guests completely unmodified — a guest's data is exactly
as isolated as a real user's, just owned by a throwaway account instead of
a durable one. The alternative (some kind of "anonymous/shared" repo
concept) would have meant re-deriving isolation logic for a second code
path; this doesn't.

**Backend:**
- `User` gains `is_guest: bool` and `last_active_at: datetime | None`
  (bumped only for guests, on every request that resolves one — real
  accounts' data is never reaped, so there's nothing to track for them).
  Since this project has no Alembic, and `create_all()` only creates
  missing *tables* (a silent no-op for a new column on an existing one —
  the exact problem Phase 2 hit before), `models/db.py::init_db()` gained a
  small idempotent `_ensure_additive_columns()` that checks
  `information_schema` and issues a plain `ALTER TABLE` for just these two
  columns if missing. Deliberately narrow — a genuinely destructive/renaming
  schema change still needs the "reset the local dev schema" approach from
  Phase 2, not this.
- `auth.py` rewritten: `get_current_user`/`CurrentUser` (used by nearly
  every route already) now **never 401s** — a real session resolves to that
  account; anyone else gets an auto-created or reused guest. New
  `get_current_real_user`/`RequireRealUser` keeps the old hard-401 behavior,
  used only where a durable identity is actually required. The old
  `get_current_user_optional`/`OptionalUser` (added in Phase 3 specifically
  so `/api/status` could work logged-out without a hard gate) is gone — now
  that `CurrentUser` itself never fails, `/api/status` just uses it
  directly, and its confluence/jira "configured" fields naturally read
  `false` for a guest with no special-casing (a guest's `UserIntegration`
  lookup is always empty, since only `RequireRealUser`-gated routes can
  ever create one).
- `routers/integrations.py`'s five routes (get/put/delete confluence,
  put/delete jira) switched from `CurrentUser` to `RequireRealUser`.
- `POST /api/repos` and `POST /api/repos/refresh` 401 with a clear message
  ("Sign in with GitHub to ingest a private repository") only when a
  `token` is actually supplied by a guest — a guest ingesting a public URL
  with no token is untouched.
- New `services/repo_lifecycle.py::delete_repo()` — fully removes one repo:
  Postgres rows across every child table in FK-safe order (no
  `ON DELETE CASCADE` is set anywhere in this schema, so this mirrors
  `indexing/run.py::extract_to_postgres()`'s existing
  SymbolEdge/Weakness-before-Symbol ordering) plus this repo's OpenSearch
  documents. Deliberately does NOT touch the on-disk git clone —
  `clone_or_open()` reuses that directory across every `Repo` row sharing a
  URL (a known pre-existing limitation, see Phase 2's notes above), so
  deleting it here could break a different user's still-live repo pointed
  at the same clone.
- New `services/guest_cleanup.py::reap_stale_guests()` — finds every guest
  whose `last_active_at` is older than `settings.guest_data_ttl_hours`
  (default 24h), deletes every repo they own via `delete_repo()`, then their
  `IngestJob`/`UsageLedger` rows and the `User` row itself.
- `main.py`: a new daemon thread (`_guest_reaper_loop`, same plain-threading
  pattern every background job in this app already uses — no new
  queue/scheduler dependency) runs `reap_stale_guests()` hourly for the
  life of the process. A failed sweep is swallowed and retried next
  interval, never crashes the loop.

**Frontend:**
- `RequireAuth.tsx`/`AuthContext.tsx` needed **zero changes** — they already
  redirect to `/login` only when `/api/auth/me` fails, and it now never
  fails. The entire existing route tree (including `Landing.tsx`, the
  add-repo form) became guest-accessible automatically as a consequence of
  the backend change alone.
- `Shell.tsx`'s sidebar user-chip branches on `user.is_guest`: a guest sees
  "Browsing as guest" + a "Sign in with GitHub" link instead of the
  avatar/username/sign-out block.
- `Landing.tsx`: a small hint under the private-repo token field for guests
  ("Sign in with GitHub to ingest a private repository") — the backend's
  401 would already surface via the existing error-display path with zero
  changes, this just avoids a wasted submit-and-fail round trip.
- `Settings.tsx`: guests see an upsell ("needs a real account — there's
  nowhere durable to keep a guest's credentials" + a sign-in link) instead
  of querying `/api/integrations` at all (which would 401 for them).
- `types.ts`'s `User` gains `is_guest: boolean`; `/api/auth/me`'s response
  includes it.

**Verified:**
- Full test suite: 101 passed, same pre-existing unrelated failures.
- HTTP-level, via a real cookie jar against the running dev backend (not
  `TestClient` — this needed real session-cookie persistence across
  requests, which `dependency_overrides` bypasses entirely): first hit to
  `/api/auth/me` with no cookie returns 200 with a fresh guest (not 401);
  a second hit with the same cookie returns the identical guest id (not a
  new one each time); `GET /api/integrations` 401s for that guest;
  `POST /api/repos` with a `token` 401s for that guest;
  `POST /api/repos` with no token (a public repo) succeeds and starts a
  real ingest job, correctly attributed to the guest's `user_id` in
  `IngestJob` and progressing normally through the pipeline; `/api/status`
  correctly reads `confluence.configured`/`jira.configured` as `false` for
  the guest.
- Direct DB test of the reaper: a synthetic stale guest (backdated
  `last_active_at`) owning a repo with a symbol, an `IngestJob`, and a
  `UsageLedger` row — after `reap_stale_guests()`, confirmed the `User`
  row, the `Repo` row, the `Symbol` row, the `IngestJob` row, and the
  `UsageLedger` row are ALL gone, while a second, freshly-active synthetic
  guest was left completely untouched.
- **Two real bugs caught and fixed during this verification, not found by
  inspection alone**:
  1. `_create_guest()`'s synthetic `github_id` was generated as
     `-random.randint(1, 2**62)` — `users.github_id` is a plain 32-bit
     Postgres `INTEGER`, not `BIGINT`, and that range overflows it outright
     (`psycopg.errors.NumericValueOutOfRange`, caught live via a real 500
     while testing the very first guest request). Fixed to
     `2**31 - 1` — still ~2.1 billion possible values, comfortably enough
     entropy for realistic guest volume, and fits the column.
  2. While restarting the dev backend to pick up code changes mid-session,
     `uvicorn --reload`'s child worker process kept running (and kept
     serving pre-edit code) after the PARENT reloader process was killed —
     `--reload` spawns the actual request-serving worker as a
     `multiprocessing.spawn` child, and killing just the parent on Windows
     doesn't clean up that child. Cost real debugging time (stale "Not
     signed in" responses that looked like the new auto-guest code hadn't
     taken effect at all) before being traced to the orphaned child PID.
     Not a code bug — a dev-workflow trap worth remembering: when `--reload`
     behaves like it isn't picking up an edit, check for an orphaned worker
     process, not just the process you think you started.

**Explicitly NOT done in this phase:**
- No frontend UI for a real user to see/manage their own repo list beyond
  the existing single-active-repo switcher from Phase 2 — a guest's
  ephemeral repos aren't distinguished from a real user's in that UI at
  all today, since a guest only ever has their own single session's repos
  in view anyway.
- No admin visibility into guest-account volume/reap activity (e.g. how
  many guests exist right now, how many were reaped last cycle) — the
  reaper logs nothing beyond swallowing its own failures. Worth adding if
  guest traffic ever becomes large enough to want to watch.
- Real per-provider GitHub rate-limit behavior for a flood of anonymous
  ingests was not stress-tested — unauthenticated GitHub API calls (issue/PR
  fetching during ingest) are capped at 60 req/hr per source IP; heavy
  anonymous traffic ingesting many repos from behind the same IP/NAT could
  hit that ceiling. Not a new problem this phase introduced (it already
  existed for any unauthenticated ingest), just newly relevant now that
  ingesting without an account is the intended, encouraged path.

---

## Phase 6b — Saved GitHub PAT

**Not in the original plan** — a direct follow-up to Phase 6: a signed-in
user was pasting their private-repo PAT fresh on every single ingest (the
token was always transient, never stored, by design). Explicit request:
let them save it once, mirroring exactly how Phase 4 already lets a user
save their own Confluence/Jira credentials.

- `UserIntegration` gains `github_pat_encrypted` — same encrypted-at-rest
  pattern as the Confluence/Jira tokens (`security.py`'s Fernet
  encrypt/decrypt). `models/db.py::_ensure_additive_columns()` extended to
  `ALTER TABLE user_integrations ADD COLUMN github_pat_encrypted` for
  existing installs (this table already existed from Phase 4).
- `services/user_integrations.py`: `github_pat_configured()`, `github_pat()`
  (decrypted), `upsert_github_pat()`, `clear_github_pat()`.
- `routers/integrations.py`: `PUT`/`DELETE /api/integrations/github`
  (`RequireRealUser`-gated, same as Confluence/Jira — a guest structurally
  can't have one). `GET /api/integrations`'s response gained a `github`
  section (`configured`/`has_token`, token itself never returned).
- `routers/api.py`: new `_effective_token(session, user, explicit_token)` —
  **an explicitly-supplied token on the ingest form always wins** (lets a
  signed-in user override their saved PAT for one specific repo without
  touching what's saved); only falls back to the user's saved PAT when the
  form field was left blank AND they aren't a guest. Used by both
  `POST /api/repos` and `POST /api/repos/refresh`. The existing
  guest-can't-use-a-token 401 check still fires on the raw form input
  before this resolution happens, unchanged from Phase 6.
- Frontend: new `GithubCard` in `Settings.tsx` (single token field, no
  base_url/email/space_key needed — GitHub's API endpoint is fixed).
  `Landing.tsx` now queries `/api/integrations` (shares Settings.tsx's
  query cache) to know whether a saved PAT exists; if so, the token field's
  placeholder changes to "Leave blank to use your saved GitHub token" and a
  hint with a link to Settings appears instead of leaving the user guessing
  whether an empty field will actually work.

**Verified:**
- Direct test of `_effective_token()`: an explicit token always wins even
  when a different one is saved; falls back to the saved, decrypted PAT
  only when the form field is blank; a guest gets `""` either way (no
  fallback exists for them since they can never have a `UserIntegration`
  row via the real API path — `RequireRealUser` blocks the only route that
  creates one).
- HTTP-level (`TestClient` + `dependency_overrides` for the real-user path,
  a real cookie jar against the live dev server for the guest-blocked
  path — mixing both in one dependency-override pass was tried first and
  produced a misleading pass/fail, since overriding two dependencies at
  once made the "guest" case never actually exercise guest logic; caught
  and corrected before trusting the result): `PUT /api/integrations/github`
  401s for a guest against the real server; for a real user, `GET` before
  saving correctly shows `configured: false`; `PUT` saves and `GET`
  reflects `configured: true, has_token: true` with the token never echoed
  back in the response body; the encrypted-then-decrypted stored value
  matches exactly what was sent; `DELETE` correctly clears it.
- `uv run python -c "import archaeologist.main"` and `tsc --noEmit` both
  clean; full test suite 101 passed, same pre-existing unrelated failures.

---

## Deviations from the original plan (so future-you isn't confused by a diff)

- `itsdangerous` added as an explicit `pyproject.toml` dependency — the plan said it was free; it isn't.
- Frontend repo-switching uses a new "activate" endpoint (bump `ingested_at`) rather than threading an explicit `repo_id` through every page as the plan originally described — same user-facing outcome, much smaller diff.
- Phase 3's plan described "add a `repo_id` filter to queries" as roughly the whole job. It wasn't — see the Phase 3 section above for the two additional pre-existing bugs (destructive unconditional index recreation on every ingest, and cross-repo evidence-doc id collisions) that had to be fixed alongside the filtering, plus the codemap-feature repo-derivation bug found while auditing every OpenSearch call site.
