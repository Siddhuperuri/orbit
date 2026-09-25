"""Response security headers.

Applied to every response including errors. These are cheap, and each closes a
class of attack that is otherwise entirely dependent on browser defaults.

The Content-Security-Policy here covers API responses only. The frontend is
served by Next.js and sets its own, stricter policy for HTML documents; the
policy below assumes the response body is JSON and locks everything down.
See docs/architecture/security.md.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# API responses never load a subresource and are never framed. Denying
# everything is correct here and is not merely a tightened default.
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

_STATIC_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": _API_CSP,
    # Explicitly surrenders powerful features the API has no use for, so a
    # future misconfiguration cannot quietly acquire them.
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, enable_hsts: bool) -> None:
        super().__init__(app)
        # HSTS is only sent over HTTPS. Sending it in development, where the app
        # is served over plain HTTP on localhost, would pin the browser to HTTPS
        # for localhost and break every other local project on that host.
        self._enable_hsts = enable_hsts

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for header, value in _STATIC_HEADERS.items():
            response.headers.setdefault(header, value)
        if self._enable_hsts:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
