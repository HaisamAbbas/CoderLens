"""OpenSearch client factory."""

from opensearchpy import OpenSearch

from archaeologist.config import settings


def get_client() -> OpenSearch:
    return OpenSearch(
        hosts=[{"host": settings.opensearch_host, "port": settings.opensearch_port}],
        http_compress=True,
        use_ssl=settings.opensearch_use_ssl,
        # Hosted OpenSearch (Bonsai, etc.) requires basic auth; the local
        # Docker instance has none configured, so this stays a no-op there.
        http_auth=(
            (settings.opensearch_user, settings.opensearch_password)
            if settings.opensearch_user else None
        ),
        verify_certs=False,
        ssl_show_warn=False,
        timeout=30,
    )
