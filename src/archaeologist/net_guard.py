"""Shared server-side-request-forgery guard for any URL this app is asked to
fetch on a user's behalf (a saved Confluence/Jira base URL, a repo clone
target). Centralized so every call site applies the same check rather than
each reimplementing (and potentially drifting from) it.
"""

import ipaddress
import socket
from urllib.parse import urlparse


def assert_public_host(hostname: str | None) -> None:
    """Raise ValueError unless `hostname` resolves only to public addresses.

    Best-effort only: this checks the address the host resolves to *right
    now* — a DNS answer can change between this check and the connection
    that follows it (DNS rebinding). Closing that fully would need a
    transport that resolves once and pins the connection to that address;
    out of scope here, but calling this again immediately before use (as
    the affected clients do) keeps the window small.
    """
    if not hostname:
        raise ValueError("URL has no host")
    try:
        addrs = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"could not resolve host: {hostname}") from exc
    for *_, sockaddr in addrs:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError(f"refusing to use a non-public address ({hostname} -> {ip})")


def assert_public_https_url(url: str) -> str:
    """Require https:// and a hostname that resolves to a public address.
    Raises ValueError otherwise. Returns the trimmed URL for convenience."""
    trimmed = url.strip()
    parsed = urlparse(trimmed)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("URL must start with https://")
    assert_public_host(parsed.hostname)
    return trimmed.rstrip("/")
