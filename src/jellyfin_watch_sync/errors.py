"""Translates common misconfiguration exceptions into a plain-language
message a user can act on, instead of a raw traceback.

Kept as pure functions (no Typer/Rich dependency) so this is unit-testable
without spinning up the CLI -- see tests/test_errors.py.
"""

from __future__ import annotations

import httpx


def describe_error(exc: Exception) -> str | None:
    """Returns a friendly diagnostic for a known, common failure mode, or
    None if this isn't one -- callers should re-raise (or let the original
    exception surface) in that case rather than hide an unfamiliar error."""
    if isinstance(exc, httpx.HTTPStatusError):
        return _describe_http_status_error(exc)
    if isinstance(exc, httpx.ConnectError):
        url = exc.request.url if exc.request is not None else None
        target = f" to {url}" if url else ""
        return (
            f"Could not connect{target}.\n"
            "Check that JELLYFIN_URL is correct (including http:// vs https://) "
            "and reachable from this machine."
        )
    if isinstance(exc, httpx.TimeoutException):
        return "The request to Jellyfin timed out. Check JELLYFIN_URL and your network connection."
    if _is_psycopg_error(exc):
        return (
            f"YAMTrack database error: {exc}\n"
            "Check that --yamtrack-dsn (or YAMTRACK_DSN) is correct and the database is reachable "
            "from this machine."
        )
    return None


def _describe_http_status_error(exc: httpx.HTTPStatusError) -> str:
    status = exc.response.status_code
    url = exc.request.url

    if status in (401, 403):
        return (
            f"Jellyfin rejected the request (HTTP {status}) for {url}.\n"
            "Check that JELLYFIN_API_KEY is correct and hasn't been revoked "
            "(Dashboard → Advanced → API Keys)."
        )
    if status == 404:
        return (
            f"Jellyfin returned 404 for {url}.\n"
            "If JELLYFIN_URL and JELLYFIN_API_KEY look right, double check JELLYFIN_USER_ID is a "
            "real user GUID -- see the README's 'Getting your Jellyfin credentials' section."
        )
    if status >= 500:
        return (
            f"Jellyfin returned a server error (HTTP {status}) for {url}.\n"
            "This is usually transient -- the built-in retry already gave up after several "
            "attempts, so the server may be overloaded or restarting. Try again shortly."
        )
    return f"Jellyfin returned an unexpected error (HTTP {status}) for {url}."


def _is_psycopg_error(exc: Exception) -> bool:
    # Duck-typed rather than `isinstance(exc, psycopg.Error)` so this module
    # (and cli.py, which uses it) never needs to import psycopg directly --
    # psycopg is an optional extra ([yamtrack-db]), and watch-restore-only
    # usage of this tool must not require it to be installed at all.
    return type(exc).__module__.split(".")[0] == "psycopg"
