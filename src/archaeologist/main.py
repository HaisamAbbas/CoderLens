"""FastAPI entry point.

Run (after `docker compose up -d` and `uv sync`):

    uv run uvicorn archaeologist.main:app --reload

The React frontend (frontend/) talks to the JSON API under /api. In dev it runs
on Vite (:5173) and proxies to this server; in prod its built bundle (frontend/dist)
is served from here, with an SPA fallback so client routes survive a refresh.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from archaeologist import __version__
from archaeologist.config import settings
from archaeologist.models.db import init_db
from archaeologist.routers import api, codemap, health


def _reap_orphaned_jobs() -> None:
    """Any job row still 'running' at startup is orphaned: every job runs in a
    daemon thread inside this single-worker process, so a restart (deploy, OOM,
    or `--reload`) kills the thread while the row keeps claiming 'running'. Flip
    those to 'error' so the UI stops polling a dead job — and, since the Bug
    Hunter page now re-attaches to any running scan, so it never shows an
    eternal spinner for work that isn't actually happening."""
    from sqlalchemy import update

    from archaeologist.models.db import session_scope
    from archaeologist.models.entities import IngestJob, JiraTicketJob, WeaknessScanJob

    with session_scope() as session:
        for model in (IngestJob, WeaknessScanJob, JiraTicketJob):
            session.execute(
                update(model)
                .where(model.status == "running")
                .values(status="error",
                        error="Interrupted by a server restart before it finished.")
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Creates any tables added to models/entities.py since the DB was last
    # initialized (e.g. a fresh `conversations` table) — a no-op otherwise,
    # since create_all only creates what's missing.
    init_db()
    _reap_orphaned_jobs()
    yield


app = FastAPI(
    title="CoderLens",
    description="Investigates a codebase and answers, with evidence, why it works the way it does.",
    version=__version__,
    lifespan=lifespan,
)

# Dev: allow the Vite dev server to call the API cross-origin. In prod the
# built frontend is served from this same app (see the SPA fallback below),
# so no extra origin is needed there — CORS_ORIGINS only matters if the
# frontend is ever deployed as a separate static site instead.
_extra_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", *_extra_origins],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(api.router)
app.include_router(codemap.router)


@app.get("/api", tags=["meta"])
def api_root() -> dict:
    return {"name": "CoderLens", "version": __version__, "env": settings.app_env}


# Prod: serve the built React app with SPA fallback. Assets are hashed under
# /assets; every other non-API path returns index.html so client-side routes work.
# NOTE: __file__-relative discovery only holds when running from the source
# checkout (local `uv run`) — a `pip install`-ed package (any real deploy)
# lands under site-packages, nowhere near frontend/, so FRONTEND_DIST must be
# set explicitly there (the Dockerfile does this).
_dist = Path(settings.frontend_dist) if settings.frontend_dist else (
    Path(__file__).resolve().parents[2] / "frontend" / "dist"
)
if _dist.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    _RESERVED = ("api", "health", "docs", "openapi.json", "redoc", "assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path.split("/", 1)[0] in _RESERVED:
            raise HTTPException(404)
        # index.html must NEVER be cached: it names the current hashed asset
        # bundle, so a stale copy pins the browser to an old build after every
        # rebuild (the assets themselves are content-hashed and cache forever).
        return FileResponse(
            str(_dist / "index.html"),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
