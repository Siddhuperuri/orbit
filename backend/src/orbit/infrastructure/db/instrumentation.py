"""Database latency, errors, and slow-query logging.

Hooks SQLAlchemy's cursor events, so every statement ORBIT runs is measured in
one place rather than by a decorator each repository would have to remember.

What is deliberately **not** here:

* **No per-table or per-query label.** Statement *type* (select, insert, ...)
  is bounded; table names or SQL fragments are not a label anyone can alert on,
  and a dynamically generated query would create a series per shape. The slow
  query log is where a specific statement is named.
* **No bound parameter values.** A slow-query record carries the statement text
  SQLAlchemy sends -- placeholders, not values -- so a document's text or an
  email address never reaches a log through this path.
* **No constraint-violation errors.** A unique-key collision is the database
  doing its job (idempotent creates rely on it). Counting those would bury the
  failures that matter.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.engine.interfaces import DBAPICursor, ExecutionContext
from sqlalchemy.pool import QueuePool

from orbit.core.logging import get_logger
from orbit.core.metrics import (
    DB_ERRORS,
    DB_POOL_CONNECTIONS,
    DB_POOL_LIMIT,
    DB_QUERY_DURATION,
)

logger = get_logger(__name__)

_STARTED_KEY = "orbit_started_at"

_STATEMENT_TYPES = frozenset({"select", "insert", "update", "delete"})

# SQLSTATE codes (https://www.postgresql.org/docs/current/errcodes-appendix.html).
_QUERY_CANCELED = "57014"  # statement_timeout, or a manual cancel
_DEADLOCK = "40P01"
_SERIALIZATION_FAILURE = "40001"
_INTEGRITY_CLASS = "23"
_CONNECTION_CLASS = "08"

#: Longest statement text a slow-query record carries.
_MAX_STATEMENT_CHARS = 400


def statement_type(statement: str) -> str:
    """The leading verb of a statement, or ``other``.

    A CTE (``WITH ...``) counts as a select: ORBIT's are read queries, and the
    distinction an operator wants is read versus write.
    """
    words = statement.lstrip().split(None, 1)
    verb = words[0].lower() if words else ""
    if verb == "with":
        return "select"
    return verb if verb in _STATEMENT_TYPES else "other"


def error_kind(sqlstate: str | None, *, is_disconnect: bool) -> str | None:
    """Classify a failed statement; ``None`` means "do not count it"."""
    if sqlstate == _QUERY_CANCELED:
        return "statement_timeout"
    if sqlstate in (_DEADLOCK, _SERIALIZATION_FAILURE):
        return "deadlock"
    if sqlstate is not None and sqlstate.startswith(_INTEGRITY_CLASS):
        return None
    if is_disconnect or (sqlstate is not None and sqlstate.startswith(_CONNECTION_CLASS)):
        return "connection"
    return "other"


def _sqlstate(exc: BaseException | None) -> str | None:
    """The SQLSTATE, wherever the asyncpg adapter chain put it."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        for attribute in ("sqlstate", "pgcode"):
            value = getattr(exc, attribute, None)
            if isinstance(value, str):
                return value
        exc = getattr(exc, "orig", None) or exc.__cause__ or getattr(exc, "__context__", None)
    return None


def instrument_engine(sync_engine: Engine, *, slow_query_threshold_ms: float) -> None:
    """Attach timing, error counting, and slow-query logging to an engine.

    Takes the *sync* engine an `AsyncEngine` wraps (`engine.sync_engine`): the
    cursor events fire there, and it keeps this testable without a database
    server.
    """
    threshold_s = slow_query_threshold_ms / 1000

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before(  # noqa: PLR0913, PLR0917 -- SQLAlchemy fixes the signature
        conn: Any,  # noqa: ANN401 -- SQLAlchemy's event signature
        cursor: DBAPICursor,
        statement: str,
        parameters: Any,  # noqa: ANN401
        context: ExecutionContext | None,
        executemany: bool,
    ) -> None:
        conn.info[_STARTED_KEY] = time.perf_counter()

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _after(  # noqa: PLR0913, PLR0917 -- SQLAlchemy fixes the signature
        conn: Any,  # noqa: ANN401
        cursor: DBAPICursor,
        statement: str,
        parameters: Any,  # noqa: ANN401
        context: ExecutionContext | None,
        executemany: bool,
    ) -> None:
        _finish(conn, statement, outcome="ok")

    @event.listens_for(sync_engine, "handle_error")
    def _error(exception_context: Any) -> None:  # noqa: ANN401
        statement = exception_context.statement
        connection = exception_context.connection
        if statement is not None and connection is not None:
            _finish(connection, str(statement), outcome="error")
        kind = error_kind(
            _sqlstate(exception_context.original_exception),
            is_disconnect=bool(exception_context.is_disconnect),
        )
        if kind is not None:
            DB_ERRORS.labels(kind=kind).inc()

    def _finish(conn: Any, statement: str, *, outcome: str) -> None:  # noqa: ANN401
        started = conn.info.pop(_STARTED_KEY, None)
        if started is None:
            return
        elapsed = time.perf_counter() - started
        operation = statement_type(statement)
        DB_QUERY_DURATION.labels(operation=operation, outcome=outcome).observe(elapsed)
        if elapsed >= threshold_s:
            logger.warning(
                "db.slow_query",
                statement_type=operation,
                outcome=outcome,
                duration_ms=round(elapsed * 1000, 1),
                threshold_ms=round(threshold_s * 1000),
                # Placeholders, never values; collapsed to one line and bounded.
                statement=" ".join(statement.split())[:_MAX_STATEMENT_CHARS],
            )


def publish_pool_gauges(sync_engine: Engine) -> None:
    """Set the pool gauges from the engine's current state.

    Called on the metrics refresh interval rather than on every checkout: pool
    state changes constantly, and a gauge only needs to be true at scrape time.
    """
    pool = sync_engine.pool
    if not isinstance(pool, QueuePool):
        return
    in_use = pool.checkedout()
    DB_POOL_CONNECTIONS.labels(state="in_use").set(in_use)
    DB_POOL_CONNECTIONS.labels(state="idle").set(pool.checkedin())
    DB_POOL_LIMIT.set(pool.size() + pool._max_overflow)
