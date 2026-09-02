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
        # Only meaningful when use_ssl is actually on (local dev's plain-HTTP
        # instance never TLS-handshakes at all, so this is a no-op there) —
        # when it IS on (a hosted instance in production), verify the
        # certificate by default instead of accepting any cert silently,
        # which would let a MITM on that connection go undetected.
        verify_certs=settings.opensearch_use_ssl,
        ssl_show_warn=settings.opensearch_use_ssl,
        timeout=30,
    )
