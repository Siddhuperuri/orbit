"""Classifying exceptions raised during processing.

The domain taxonomy (`domain/processing/failures.py`) covers every error ORBIT
raises on purpose. This covers the rest: exceptions from drivers, SDKs, and
the task runtime, which only the infrastructure layer may import. Anything not
recognised here is a **defect** -- deliberately the default, because guessing
"transient" for an unknown exception would retry a bug five times, and
guessing "permanent" would blame the user's file for ORBIT's mistake.
"""

from __future__ import annotations

import botocore.exceptions
import httpx
import sqlalchemy.exc
from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded

from orbit.domain.errors import (
    AIProviderUnavailableError,
    DatabaseUnavailableError,
    DependencyUnavailableError,
    DocumentLimitExceededError,
    DocumentProcessingTimeoutError,
    OrbitError,
    StorageUnavailableError,
)
from orbit.domain.processing.failures import (
    ProcessingFailure,
    classify_orbit_error,
    describe_exception,
)

#: PostgreSQL SQLSTATEs that mean "try the transaction again": serialization
#: failure, deadlock, and the connection-exception class.
_TRANSIENT_SQLSTATES = frozenset({"40001", "40P01", "08000", "08003", "08006", "57P01", "53300"})

_TIMEOUT_MESSAGE = "This document took too long to process. It may be too large or too complex."
_TOO_COMPLEX_MESSAGE = "This document is too complex to process."


class PipelineFailureClassifier:
    # A decision table: one early return per exception family reads more
    # plainly than any restructuring that would satisfy the counter.
    def classify(self, exc: BaseException) -> ProcessingFailure:  # noqa: PLR0911
        if isinstance(exc, OrbitError):
            return classify_orbit_error(exc)
        if isinstance(exc, (SoftTimeLimitExceeded, TimeLimitExceeded)):
            return ProcessingFailure.permanent(DocumentProcessingTimeoutError(_TIMEOUT_MESSAGE))
        if isinstance(exc, MemoryError):
            return ProcessingFailure.permanent(DocumentLimitExceededError(_TOO_COMPLEX_MESSAGE))
        if _is_transient_database_error(exc):
            return ProcessingFailure.transient(
                code=DatabaseUnavailableError.code, detail=describe_exception(exc)
            )
        if isinstance(
            exc,
            (
                botocore.exceptions.ConnectionError,
                botocore.exceptions.HTTPClientError,
                botocore.exceptions.ReadTimeoutError,
            ),
        ):
            return ProcessingFailure.transient(
                code=StorageUnavailableError.code, detail=describe_exception(exc)
            )
        if isinstance(exc, httpx.TransportError):
            return ProcessingFailure.transient(
                code=AIProviderUnavailableError.code, detail=describe_exception(exc)
            )
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return ProcessingFailure.transient(
                code=DependencyUnavailableError.code, detail=describe_exception(exc)
            )
        return ProcessingFailure.defect(detail=describe_exception(exc))


def _is_transient_database_error(exc: BaseException) -> bool:
    if isinstance(exc, sqlalchemy.exc.TimeoutError):
        # Pool checkout timeout: the database is saturated, not wrong.
        return True
    if not isinstance(exc, sqlalchemy.exc.DBAPIError):
        return False
    if exc.connection_invalidated or isinstance(
        exc, (sqlalchemy.exc.OperationalError, sqlalchemy.exc.InterfaceError)
    ):
        return True
    sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    return sqlstate in _TRANSIENT_SQLSTATES
