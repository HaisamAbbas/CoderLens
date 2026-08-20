"""OpenSearch client factory."""

from opensearchpy import OpenSearch

from archaeologist.config import settings


def get_client() -> OpenSearch:
    return OpenSearch(
        hosts=[{"host": settings.opensearch_host, "port": settings.opensearch_port}],
        http_compress=True,
        use_ssl=settings.opensearch_use_ssl,
        verify_certs=False,
        ssl_show_warn=False,
        timeout=30,
    )
