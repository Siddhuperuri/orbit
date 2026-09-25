"""Exception handlers.

Every failure leaves the application through exactly one of these, so the error
contract cannot drift endpoint by endpoint.

The rule that shapes this module: **an unexpected exception never reaches the
client**. It becomes a generic 500 carrying only the request id, while the
exception, its traceback, and its structured context are logged server-side. A
user can quote the request id and support can find the incident, without a stack
trace, SQL fragment, or filesystem path ever crossing the boundary.

See docs/decisions/0014-error-handling-strategy.md.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from orbit.api.deps import get_client_ip
from orbit.api.v1.schemas.errors import ErrorBody, ErrorResponse, FieldError
from orbit.core.logging import bind_correlation, get_logger
from orbit.core.metrics import HTTP_ERRORS
from orbit.domain.errors import (
    DependencyUnavailableError,
    OrbitError,
    PermissionDeniedError,
    RateLimitedError,
)
from orbit.domain.ports.audit import AuditAction, AuditEvent, AuditSink

logger = get_logger(__name__)

_HTTP_INTERNAL_ERROR = 500

# Generic text for unhandled exceptions. Deliberately says nothing about what
# failed: the caller cannot act on the detail, and the detail is exactly what
# must not be disclosed.
_INTERNAL_MESSAGE = "An unexpected error occurred. Quote the request id if you report this."

# Starlette raises bare HTTPExceptions for routing outcomes before any of our
# code runs, so those statuses need codes of their own.
_STARLETTE_STATUS_CODES: dict[int, str] = {
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    422: "VALIDATION_ERROR",
}


def _request_id(request: Request) -> str:
    # Present for every request that passed RequestContextMiddleware. The
    # fallback covers exceptions raised before it runs, where an empty string is
    # more honest than inventing an id that appears in no log line.
    request_id: str = getattr(request.state, "request_id", "")
    return request_id


def _envelope(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    details: list[FieldError] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(code=code, message=message, request_id=request_id, details=details)
    )
    return JSONResponse(
        status_code=status_code,
        # `exclude_none` would drop `details`; it is kept explicitly null so the
        # response shape is identical for every error, which clients rely on.
        content=body.model_dump(mode="json"),
    )


async def handle_orbit_error(request: Request, exc: Exception) -> JSONResponse:
    """Map a deliberate domain error to its response."""
    assert isinstance(exc, OrbitError)  # noqa: S101 -- registered only for OrbitError

    HTTP_ERRORS.labels(code=exc.code).inc()
    log = logger.bind(error_code=exc.code, **exc.context)
    if exc.http_status >= _HTTP_INTERNAL_ERROR:
        # A 5xx OrbitError is still our fault; it gets a traceback.
        log.error("request.failed", exc_info=exc)
    else:
        # Client errors are expected traffic, not incidents. Logged at info so
        # they remain queryable without polluting the error rate.
        log.info("request.rejected")

    response = _envelope(
        status_code=exc.http_status,
        code=exc.code,
        message=exc.message,
        request_id=_request_id(request),
    )

    if isinstance(exc, PermissionDeniedError):
        await _record_permission_denied(request, exc)

    # `Retry-After` is the only piece of an error's context that crosses the
    # boundary, and it is safe precisely because it says nothing about the
    # account -- only about the clock.
    if isinstance(exc, RateLimitedError) and exc.retry_after_seconds is not None:
        response.headers["Retry-After"] = str(exc.retry_after_seconds)
    elif isinstance(exc, DependencyUnavailableError):
        # A provider that said how long to wait (GENERATION_RATE_LIMITED):
        # passing it on is what stops clients retrying into the same 429.
        retry_after = exc.context.get("retry_after_seconds")
        if isinstance(retry_after, int) and retry_after >= 0:
            response.headers["Retry-After"] = str(retry_after)

    return response


async def _record_permission_denied(request: Request, exc: PermissionDeniedError) -> None:
    """Audit a 403 -- and only a 403, never a 404.

    A `PermissionDeniedError` means a *member* of the workspace attempted
    something their role forbids, which is the signal worth keeping. A 404 from
    the tenant boundary means an outsider guessed an id and learned nothing;
    recording those would bury the interesting events under scan noise.

    Reads the sink off application state rather than taking it as a parameter
    because an exception handler's signature is fixed by Starlette. Missing
    state is tolerated: some tests build an app without a container, and an
    audit gap must never turn an authorization failure into a 500.
    """
    container = getattr(request.app.state, "container", None)
    audit: AuditSink | None = getattr(container, "audit", None)
    if audit is None:
        return

    user = getattr(request.state, "user_id", None)
    await audit.record(
        AuditEvent(
            action=AuditAction.PERMISSION_DENIED,
            actor_user_id=user,
            client_ip=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            request_id=_request_id(request),
            metadata={
                "path": request.url.path,
                "method": request.method,
                # `permission` and `role` come from `AccessContext.require`.
                "permission": exc.context.get("permission"),
                "role": exc.context.get("role"),
                "workspace_id": exc.context.get("workspace_id"),
            },
        )
    )


async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """Convert FastAPI's validation failure into the standard envelope."""
    assert isinstance(exc, RequestValidationError)  # noqa: S101

    details = [
        FieldError(
            # loc is a tuple like ("body", "email"); joined into a dotted path.
            field=".".join(str(part) for part in error.get("loc", ())) or "request",
            message=str(error.get("msg", "Invalid value.")),
        )
        for error in exc.errors()
    ]
    HTTP_ERRORS.labels(code="VALIDATION_ERROR").inc()
    logger.info("request.invalid", field_count=len(details))
    return _envelope(
        status_code=422,
        code="VALIDATION_ERROR",
        message="The request contains invalid values.",
        request_id=_request_id(request),
        details=details,
    )


async def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    """Give Starlette's own HTTP exceptions the same envelope.

    Without this, a 404 for an unknown route would return Starlette's
    ``{"detail": ...}`` shape and a client would need to parse two formats.
    """
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101

    code = _STARLETTE_STATUS_CODES.get(exc.status_code, f"HTTP_{exc.status_code}")
    HTTP_ERRORS.labels(code=code).inc()
    # exc.detail is framework-generated text, never user input, so it is safe to
    # return -- but only for 4xx. A 5xx detail could carry internals.
    message = str(exc.detail) if exc.status_code < _HTTP_INTERNAL_ERROR else _INTERNAL_MESSAGE
    return _envelope(
        status_code=exc.status_code,
        code=code,
        message=message,
        request_id=_request_id(request),
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Last resort: an exception nobody anticipated.

    Reaching here is a defect. The full exception is logged; the client gets a
    request id and nothing else.
    """
    # This handler runs in Starlette's outermost middleware, after
    # `RequestContextMiddleware` has unwound and cleared its context -- so
    # without this, the one record that carries the traceback would be the one
    # record with no `request_id`, and the client's quoted id would match no
    # log line. The middleware leaves the identifiers on the request for us.
    saved = getattr(request.state, "correlation", None)
    if isinstance(saved, dict):
        bind_correlation(**saved)
    HTTP_ERRORS.labels(code="INTERNAL_ERROR").inc()
    logger.exception("request.unhandled_exception", path=request.url.path)
    response = _envelope(
        status_code=_HTTP_INTERNAL_ERROR,
        code="INTERNAL_ERROR",
        message=_INTERNAL_MESSAGE,
        request_id=_request_id(request),
    )
    # The request-context middleware's own header injection never sees this
    # response either, and a client is told to quote this id.
    response.headers["X-Request-ID"] = _request_id(request)
    return response


def register_exception_handlers(app: FastAPI) -> None:
    """Install every handler. Called once, by the application factory."""
    app.add_exception_handler(OrbitError, handle_orbit_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    # Catch-all. Registered last so more specific handlers take precedence.
    app.add_exception_handler(Exception, handle_unexpected_error)
