"""Shared rate limiter for the LLM-spending and ingest-triggering routes.

Every route that reaches an LLM provider or clones a repository is
reachable with no login at all (see auth.get_current_user's "never 401s"
docstring) — without a limit, an anonymous script can flood any of them and
either exhaust the operator's provider quota/billing or fill disk with
clones. Keyed by remote address rather than the resolved user id: it must
work before any session/DB lookup happens, and a guest account is trivially
re-mintable per request anyway (see services/guest_cleanup.py), so an
account-keyed limit alone wouldn't actually bound anything.

This is a per-process, in-memory limiter (slowapi's default `limits`
in-memory storage) — fine for this app's single-worker deployment; it
would need Redis-backed storage (limits already supports it) to hold
across multiple workers/instances.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
