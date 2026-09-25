"""Request correlation, access logging, and HTTP metrics.

The outermost middleware. It assigns a ``request_id``, binds it so every log
record emitted while handling the request carries it automatically, echoes it
back in a response header, and emits exactly one access-log line and one set of
HTTP metrics per request.

Written as pure ASGI rather than ``BaseHTTPMiddleware`` for three reasons that
each matter operationally:

* **Streaming.** ``BaseHTTPMiddleware`` returns as soon as the response
  *starts*, so a streamed answer (SSE) would be timed to its first byte and a
  60-second generation would log as 5 ms. Here the clock stops at the last body
  chunk, or when the client goes away.
* **Correlation.** Identifiers bound while handling the request (user,
  workspace, document, operation) must be on the access-log line, which is
  emitted here, after the handler finished. Downstream ``BaseHTTPMiddleware``
  runs the app in a child task whose context is a *copy*; see
  ``orbit.core.logging.begin_request_context`` for how writes travel back.
* **Cost.** One fewer task and queue hop per request.

Correlation matters most for work that outlives the request: the id assigned
here is carried into the Celery task payload, so a document's upload request and
its worker attempts share one identifier
(docs/decisions/0015-observability-strategy.md).
"""

from __future__ import annotations

import time
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from orbit.core.ids import is_ulid, new_ulid
from orbit.core.logging import begin_request_context, clear_correlation, get_logger
from orbit.core.metrics import HTTP_IN_FLIGHT, HTTP_REQUEST_DURATION, HTTP_REQUESTS

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

# Paths excluded from access logging. Orchestrator probes hit these every few
# seconds; logging them buries real traffic and costs real money at volume.
# They are still *measured*: a probe endpoint that turns slow is a finding.
_UNLOGGED_PATHS = frozenset({"/healthz", "/readyz", "/metrics"})

# Where a request that matched no route is counted. Never the raw path: a
# scanner can request an unbounded number of distinct URLs, and each would
# otherwise become its own time series.
UNMATCHED_ROUTE = "unmatched"

# What a request that ended without a status is recorded as: the client went
# away before we answered. The convention (nginx's) is 499.
_CLIENT_CLOSED = 499
_SERVER_ERROR = 500


class RequestContextMiddleware:
    """Assigns and propagates the request correlation id; logs and measures."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        trust_incoming_header: bool = False,
        slow_request_threshold_ms: float = 2000.0,
    ) -> None:
        self.app = app
        # Only enable behind a trusted proxy that sets or strips this header.
        # Accepting a client-supplied id on an internet-facing service lets a
        # caller collide their requests with someone else's in the logs.
        self._trust_incoming_header = trust_incoming_header
        self._slow_ms = slow_request_threshold_ms

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = self._resolve_request_id(scope)
        fields = begin_request_context(request_id)
        # `request.state` is a view over this dict, which is how the exception
        # handlers and the SSE generator learn the id.
        scope.setdefault("state", {})["request_id"] = request_id

        status_code: int | None = None
        finished = False

        async def send_with_id(message: Message) -> None:
            nonlocal status_code, finished
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                finished = True
            await send(message)

        method: str = scope["method"]
        path: str = scope["path"]
        started = time.perf_counter()
        error_type: str | None = None
        HTTP_IN_FLIGHT.inc()
        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:
            # The exception handler registered for `Exception` produces the
            # response and logs the traceback (it runs in ServerErrorMiddleware,
            # outside this one). Only the timing and classification are ours.
            error_type = type(exc).__name__
            # The handler runs after this frame unwinds and cannot see our
            # context, so hand it the identifiers explicitly.
            scope["state"]["correlation"] = {"request_id": request_id, **fields}
            status_code = _SERVER_ERROR
            raise
        finally:
            HTTP_IN_FLIGHT.dec()
            elapsed = time.perf_counter() - started
            if status_code is None:
                status_code = _CLIENT_CLOSED
            route = _route_template(scope)
            HTTP_REQUESTS.labels(method=method, route=route, status=str(status_code)).inc()
            HTTP_REQUEST_DURATION.labels(method=method, route=route).observe(elapsed)
            if path not in _UNLOGGED_PATHS:
                self._log(
                    method=method,
                    path=path,
                    route=route,
                    status_code=status_code,
                    completed=finished,
                    elapsed_ms=elapsed * 1000,
                    error_type=error_type,
                    fields=fields,
                )
            clear_correlation()

    def _log(  # noqa: PLR0913 -- one record, assembled once
        self,
        *,
        method: str,
        path: str,
        route: str,
        status_code: int,
        completed: bool,
        elapsed_ms: float,
        error_type: str | None,
        fields: dict[str, Any],
    ) -> None:
        # The record is emitted after the handler returned, in a context whose
        # own bindings the handler could not touch; the shared `fields` carry
        # the user, workspace, document, and operation it bound.
        log = logger.bind(**fields)
        record: dict[str, Any] = {
            "method": method,
            "path": path,
            "route": route,
            "status_code": status_code,
            "duration_ms": round(elapsed_ms, 2),
        }
        if not completed and status_code != _CLIENT_CLOSED:
            # Headers went out but the body never finished: a dropped client
            # or a stream that failed midway.
            record["response_incomplete"] = True
        if error_type is not None:
            record["error_type"] = error_type

        if status_code >= _SERVER_ERROR:
            log.warning("http.request", **record)
        elif elapsed_ms >= self._slow_ms:
            log.warning("http.request", slow=True, **record)
        else:
            log.info("http.request", **record)

    def _resolve_request_id(self, scope: Scope) -> str:
        if self._trust_incoming_header:
            incoming = Headers(scope=scope).get(REQUEST_ID_HEADER, "")
            # Validated, never echoed raw: an unchecked header would let a caller
            # inject newlines or unbounded length into every log record.
            if is_ulid(incoming):
                return incoming
        return new_ulid()


def _route_template(scope: Scope) -> str:
    """The matched route's path template, e.g. ``/workspaces/{workspace_id}``."""
    route = scope.get("route")
    template = getattr(route, "path", None)
    return template if isinstance(template, str) else UNMATCHED_ROUTE
