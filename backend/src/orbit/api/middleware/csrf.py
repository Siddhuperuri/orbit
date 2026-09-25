"""CSRF defence in depth (ADR-0009).

`SameSite=Lax` on both auth cookies is the primary control: browsers do not
attach them to a cross-site `POST`/`PUT`/`PATCH`/`DELETE`. This middleware is
the second, independent layer -- for older browsers, and for the day a
`SameSite=None` exception is ever needed for some integration.

The rule: a mutating request that carries either auth cookie must have an
`Origin` header naming this same host, if it sends one at all. A request with
no cookie (a Bearer-token API client) is exempt -- there is no ambient
credential for a forged cross-site form to ride on. A request with no `Origin`
header is also allowed through here: most same-site navigations omit it, and
`SameSite=Lax` is already the control for that case.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from orbit.api.cookies import ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME
from orbit.api.v1.schemas.errors import ErrorBody, ErrorResponse
from orbit.core.logging import get_logger

logger = get_logger(__name__)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _normalise_origin(origin: str) -> str:
    """Origins compare as whole `scheme://host:port` strings, so nothing looser
    than an exact match can ever be trusted: `http://localhost:3000` does not
    vouch for `https://localhost:3000`, `http://localhost:3001`, or a subdomain."""
    return origin.strip().rstrip("/").lower()


class CsrfOriginCheckMiddleware(BaseHTTPMiddleware):
    """Rejects a cookie-authenticated mutation whose `Origin` is not ours.

    "Ours" means the request's own `Host`, or an origin the operator has
    explicitly listed in `trusted_origins`.

    The second case exists for the development proxy. Next.js rewrites `/api/*`
    to this server and, doing so, replaces `Host` with the backend's (`:8000`)
    while the browser -- correctly -- sends `Origin: http://localhost:3000`.
    Comparing the two would reject every authenticated write from the frontend
    (ADR-0009 puts both behind one origin; a rewrite cannot preserve `Host`, a
    reverse proxy can). `ORBIT_CORS_ALLOWED_ORIGINS` already names exactly the
    origins the operator has approved for browser access, so it is the source
    for this list. Configuration validation forbids `*` and forbids any value
    in production, so a production deployment behaves exactly as before: only a
    matching `Host` is accepted.
    """

    def __init__(self, app: ASGIApp, *, trusted_origins: Collection[str] = ()) -> None:
        super().__init__(app)
        self._trusted_origins = frozenset(_normalise_origin(origin) for origin in trusted_origins)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method not in _SAFE_METHODS and self._is_forged(request):
            logger.warning(
                "http.csrf_origin_mismatch",
                method=request.method,
                path=request.url.path,
                origin=request.headers.get("origin"),
            )
            return self._rejection(request)
        return await call_next(request)

    def _rejection(self, request: Request) -> JSONResponse:
        """Reject in the standard envelope, not as a bare 403.

        This runs as middleware, outside the exception handlers, so it has to
        build the envelope itself -- and it must, or this is the one failure
        in the API whose body a client cannot parse the same way as every
        other. The request id is read from `request.state` when
        `RequestContextMiddleware` has already run; an empty string is more
        honest than inventing one that appears in no log line.
        """
        body = ErrorResponse(
            error=ErrorBody(
                code="CSRF_ORIGIN_MISMATCH",
                message="This request was rejected because it appears to come from another site.",
                request_id=getattr(request.state, "request_id", ""),
                details=None,
            )
        )
        return JSONResponse(status_code=403, content=body.model_dump(mode="json"))

    def _is_forged(self, request: Request) -> bool:
        origin = request.headers.get("origin")
        if origin is None:
            return False

        carries_cookie = (
            ACCESS_COOKIE_NAME in request.cookies or REFRESH_COOKIE_NAME in request.cookies
        )
        if not carries_cookie:
            return False

        if _normalise_origin(origin) in self._trusted_origins:
            return False

        origin_host = urlsplit(origin).netloc
        return origin_host != request.headers.get("host", "")
