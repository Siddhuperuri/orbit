"""Structured logging.

JSON in deployed environments, human-readable locally. Every record carries the
correlation identifiers bound to the current context, so one document's
lifecycle -- upload request, enqueue, worker attempts, final outcome -- is
retrievable with a single field query rather than by grepping timestamps across
two processes.

Redaction is structural. A processor drops known-sensitive keys wherever they
appear, because relying on every developer to remember not to log a token is not
a control. See docs/decisions/0015-observability-strategy.md.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from orbit import __version__
from orbit.core.config import Environment, LogFormat, Settings

# Keys whose values never appear in a log record, at any nesting depth.
# Matching is on the key name, lowercased -- values are never inspected, because
# inspecting them would mean the secret had already been formatted.
_REDACTED_KEYS = frozenset(
    {
        "password",
        "current_password",
        "new_password",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "authorization",
        "cookie",
        "set_cookie",
        "secret",
        "secret_key",
        "api_key",
        "openai_api_key",
        "client_secret",
        "presigned_url",
        "signature",
        # Document text is never a log field; only its length and hash are.
        "content",
        "text",
        "chunk_text",
    }
)

_REDACTED = "[redacted]"

# Bound by middleware and by the worker's task base class. Anything present here
# is attached to every record emitted while that context is active.
#
# `operation` names the unit of work being done -- an endpoint, a pipeline run, a
# recovery sweep -- so "everything that happened during document.process" is a
# field query. It is a bounded vocabulary (code names, never user input).
_CORRELATION_KEYS = (
    "request_id",
    "user_id",
    "workspace_id",
    "document_id",
    "job_id",
    "operation",
)

# Shared by reference with every task the request spawns. structlog's own
# contextvars are *copied* into child tasks, so a value bound inside one
# (Starlette runs `BaseHTTPMiddleware` downstream in a child task) is invisible
# to the code that started it -- and that code is the access-log middleware,
# which must emit the one line per request with the user and workspace on it.
# A mutable dict is not copied, only referenced, so writes travel back up.
_request_fields: ContextVar[dict[str, Any] | None] = ContextVar(
    "orbit_request_fields", default=None
)


def _redact_sensitive(_logger: object, _method: str, event_dict: EventDict) -> EventDict:
    """Replace sensitive values anywhere in the event, including nested mappings."""

    def scrub(value: object) -> object:
        if isinstance(value, MutableMapping):
            return {
                key: (_REDACTED if str(key).lower() in _REDACTED_KEYS else scrub(item))
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return type(value)(scrub(item) for item in value)
        return value

    return {
        key: (_REDACTED if key.lower() in _REDACTED_KEYS else scrub(value))
        for key, value in event_dict.items()
    }


def _add_service_context(settings: Settings) -> Processor:
    """Bind immutable service identity to every record.

    ``version`` matters during an incident: without it, a log line cannot be
    attributed to the build that produced it.
    """

    def processor(_logger: object, _method: str, event_dict: EventDict) -> EventDict:
        event_dict.setdefault("service", settings.service_name)
        event_dict.setdefault("environment", settings.env.value)
        event_dict.setdefault("version", __version__)
        return event_dict

    return processor


def configure_logging(settings: Settings) -> None:
    """Configure structlog and route the stdlib logging module through it.

    Third-party libraries log through stdlib ``logging``; without this those
    records would bypass the processor chain and appear unstructured and
    unredacted alongside our own.
    """
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_service_context(settings),
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _redact_sensitive,
    ]

    is_json = settings.log_format is LogFormat.JSON
    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if is_json
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    # Everything -- ORBIT's own loggers and third-party stdlib loggers alike --
    # is emitted through a single stdlib handler. Routing structlog through
    # stdlib rather than printing directly is what lets one formatter apply the
    # same processor chain, and therefore the same redaction, to both.
    #
    # `add_logger_name` requires a logger that has a `.name`, which is why the
    # factory must be the stdlib one. Pairing it with a PrintLogger raises
    # AttributeError on the first record -- and only on records that reach the
    # processor, so it surfaces at the worst possible moment.
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # Caching is a per-call speed-up worth having in a deployed process,
        # but it freezes each logger's processor chain at first use. A test
        # suite configures logging many times (once per app it builds), and a
        # frozen logger then escapes `structlog.testing.capture_logs` entirely
        # -- so assertions about what the pipeline logs would depend on test
        # order. The test environment therefore never caches.
        cache_logger_on_first_use=settings.env is not Environment.TEST,
    )

    formatter_processors: list[Processor] = [
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
    ]
    if is_json:
        # ConsoleRenderer formats exceptions itself; the JSON renderer needs the
        # traceback flattened into a string field first.
        formatter_processors.append(structlog.processors.format_exc_info)
    formatter_processors.append(renderer)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            # Applied to records from libraries that never touched structlog, so
            # a botocore warning is redacted and structured like everything else.
            foreign_pre_chain=shared,
            processors=formatter_processors,
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    # With stdlib BoundLogger, level filtering happens here rather than in a
    # structlog wrapper class.
    root.setLevel(logging.getLevelNamesMapping()[settings.log_level])

    # uvicorn installs its own handlers; clearing them prevents duplicate output.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        stdlib_logger = logging.getLogger(name)
        stdlib_logger.handlers = []
        stdlib_logger.propagate = True

    # SQLAlchemy's engine logger is noisy at INFO and duplicates ORBIT_DB_ECHO.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound logger. ``name`` should be the module's ``__name__``."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


def bind_correlation(**values: Any) -> None:  # noqa: ANN401 -- values are heterogeneous ids
    """Bind correlation identifiers for the remainder of this context.

    Only recognised keys are bound, so a typo cannot silently create a parallel
    field that no dashboard queries.
    """
    recognised = {k: v for k, v in values.items() if k in _CORRELATION_KEYS and v is not None}
    structlog.contextvars.bind_contextvars(**recognised)
    shared = _request_fields.get()
    if shared is not None:
        shared.update(recognised)


def begin_request_context(request_id: str) -> dict[str, Any]:
    """Start a fresh correlation context for one request and return its fields.

    The returned dict is live: identifiers bound later, from any task the
    request spawns, appear in it. The caller reads it when the request ends.
    """
    clear_correlation()
    fields: dict[str, Any] = {}
    _request_fields.set(fields)
    bind_correlation(request_id=request_id)
    return fields


def current_correlation() -> dict[str, Any]:
    """A snapshot of the identifiers bound to this context."""
    bound = structlog.contextvars.get_contextvars()
    return {k: v for k, v in bound.items() if k in _CORRELATION_KEYS}


@contextmanager
def operation(name: str) -> Iterator[None]:
    """Name the unit of work for every record emitted inside the block.

    Restores the previous value on exit, so an operation nested inside another
    (a recovery sweep re-publishing a job) does not leave the outer one
    mislabelled afterwards.
    """
    previous = structlog.contextvars.get_contextvars().get("operation")
    bind_correlation(operation=name)
    try:
        yield
    finally:
        if previous is None:
            structlog.contextvars.unbind_contextvars("operation")
        else:
            bind_correlation(operation=previous)


def current_request_id() -> str | None:
    """The request id bound to this context, if any.

    Read by use cases that hand work to the worker, so the id can be written to
    the job row and carried in the queue payload (ADR-0015) without every use
    case signature growing a `request_id` parameter.
    """
    value = structlog.contextvars.get_contextvars().get("request_id")
    return value if isinstance(value, str) else None


def clear_correlation() -> None:
    """Clear bound correlation identifiers.

    Required in the worker, where a process is reused across tasks and stale
    context would misattribute one document's failure to another.
    """
    structlog.contextvars.clear_contextvars()
    _request_fields.set(None)
