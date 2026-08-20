"""Health endpoints.

- GET /health       — liveness (the app is up)
- GET /health/deps  — checks Postgres, OpenSearch, and Redis connectivity

Used in Phase 0 to confirm the Docker stack is reachable from the app.
"""

from fastapi import APIRouter

from archaeologist.config import settings

router = APIRouter(prefix="/health", tags=["health"])


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
            connect_timeout=3,
        ) as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001 - surface any connection failure
        return {"status": "error", "detail": str(exc)}


def _check_opensearch() -> dict:
    try:
        from archaeologist.indexing.opensearch_client import get_client

        # Use the app's own client (urllib3) rather than httpx: on some Windows
        # setups httpx hangs resolving localhost, while the search stack works.
        client = get_client()
        resp = client.cluster.health(timeout=3)
        return {"status": "ok", "cluster": resp.get("status")}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc)}


def _check_redis() -> dict:
    try:
        import redis

        client = redis.Redis(
            host=settings.redis_host, port=settings.redis_port, socket_timeout=3
        )
        client.ping()
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc)}


@router.get("/deps")
def deps() -> dict:
    checks = {
        "postgres": _check_postgres(),
        "opensearch": _check_opensearch(),
        "redis": _check_redis(),
    }
    overall = "ok" if all(c["status"] == "ok" for c in checks.values()) else "degraded"
    return {"status": overall, "checks": checks}
