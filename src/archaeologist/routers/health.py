"""Health endpoints.

- GET /health       — liveness (the app is up)
- GET /health/deps  — checks Postgres, OpenSearch, and Redis connectivity

Used in Phase 0 to confirm the Docker stack is reachable from the app.
Both routes are intentionally unauthenticated (matched by /health/deps not
requiring RequireRealUser either) so they work as a plain uptime probe —
/health/deps's per-service detail is logged server-side rather than
returned to the caller, so it stays useful to an operator's own monitoring
without handing an anonymous caller connection strings, internal
hostnames, or credentials embedded in a raw driver exception.
"""

import logging

from fastapi import APIRouter

from archaeologist.config import settings

router = APIRouter(prefix="/health", tags=["health"])
_logger = logging.getLogger("archaeologist")


@router.get("")
def health() -> dict:
    return {"status": "ok"}


def _check_postgres() -> dict:
    try:
        import psycopg

        with psycopg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            user=settings.postgres_user,
            password=settings.postgres_password,
            dbname=settings.postgres_db,
            sslmode=settings.postgres_sslmode or None,
            connect_timeout=3,
        ) as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}
    except Exception:  # noqa: BLE001 - logged below, never returned to the caller
        _logger.warning("health/deps: postgres check failed", exc_info=True)
        return {"status": "error"}


def _check_opensearch() -> dict:
    try:
        from archaeologist.indexing.opensearch_client import get_client

        # Use the app's own client (urllib3) rather than httpx: on some Windows
        # setups httpx hangs resolving localhost, while the search stack works.
        client = get_client()
        resp = client.cluster.health(timeout=3)
        return {"status": "ok", "cluster": resp.get("status")}
    except Exception:  # noqa: BLE001
        _logger.warning("health/deps: opensearch check failed", exc_info=True)
        return {"status": "error"}


def _check_redis() -> dict:
    try:
        import redis

        client = redis.Redis(
            host=settings.redis_host, port=settings.redis_port, socket_timeout=3
        )
        client.ping()
        return {"status": "ok"}
    except Exception:  # noqa: BLE001
        _logger.warning("health/deps: redis check failed", exc_info=True)
        return {"status": "error"}


@router.get("/deps")
def deps() -> dict:
    checks = {
        "postgres": _check_postgres(),
        "opensearch": _check_opensearch(),
        "redis": _check_redis(),
    }
    overall = "ok" if all(c["status"] == "ok" for c in checks.values()) else "degraded"
    return {"status": overall, "checks": checks}
