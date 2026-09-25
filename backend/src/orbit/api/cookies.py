"""Auth cookie transport (ADR-0009).

Both tokens travel as `HttpOnly` cookies rather than in a JSON response body:
neither is ever readable by JavaScript, which is what keeps an XSS bug from
being able to exfiltrate a portable credential. This module is the only place
that names the cookie keys, paths, and attributes, so the set/clear pair can
never drift out of sync.
"""

from __future__ import annotations

from fastapi import Response

from orbit.application.auth.session import IssuedSession
from orbit.core.config import Settings

ACCESS_COOKIE_NAME = "orbit_access"
REFRESH_COOKIE_NAME = "orbit_refresh"

# Host-only cookies (no `Domain` attribute) -- the narrowest scope available.
# Path is the versioned API prefix, not "/", so neither cookie is sent to the
# frontend's own static assets.
ACCESS_COOKIE_PATH = "/api"
REFRESH_COOKIE_PATH = "/api/v1/auth"


def set_session_cookies(response: Response, session: IssuedSession, settings: Settings) -> None:
    """Attach both cookies to a login or refresh response."""
    # `secure=True` unconditionally: ORBIT is single-origin and TLS-terminated
    # in every environment that matters, including local dev behind the
    # Next.js proxy (docs/architecture/security.md). A cookie sent over plain
    # HTTP is a cookie sent in cleartext on the wire.
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        session.access_token,
        max_age=settings.access_token_ttl_seconds,
        path=ACCESS_COOKIE_PATH,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        session.refresh_token,
        max_age=settings.refresh_token_ttl_seconds,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=True,
        samesite="lax",
    )


def clear_session_cookies(response: Response) -> None:
    """Clear both cookies on logout.

    The client cannot delete an `HttpOnly` cookie itself, so logout must be a
    server round trip -- this is that round trip's other half. Attributes must
    match what `set_session_cookies` used, or the browser treats it as a
    different cookie and leaves the original in place.
    """
    response.delete_cookie(ACCESS_COOKIE_NAME, path=ACCESS_COOKIE_PATH, samesite="lax")
    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH, samesite="lax")
