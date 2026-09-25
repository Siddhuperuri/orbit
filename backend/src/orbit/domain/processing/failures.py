"""Failure classification for document processing.

Every way a processing attempt can end badly is exactly one of three kinds, and
the kind alone decides what happens next (docs/architecture/data-flow.md):

| Kind        | Meaning                   | Next                                     |
|-------------|---------------------------|------------------------------------------|
| `TRANSIENT` | a dependency is unwell    | retry with backoff; document stays PENDING |
| `PERMANENT` | the document is the problem | FAILED now, with a reason the user can act on |
| `DEFECT`    | ORBIT is wrong            | FAILED now, generic reason, full detail logged |

The taxonomy is the same one the HTTP layer uses (ADR-0014): what the API calls
retryable is exactly what the worker retries.

A `ProcessingFailure` carries two messages for two audiences, and they must
never be confused. `user_message` is stored on the version and shown in the
product; `detail` goes to the job row and the logs, for an operator.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from orbit.domain.errors import DocumentProcessingError, OrbitError

#: Operator detail is bounded: an exception message can be arbitrarily long, and
#: a job row is not a log store.
MAX_FAILURE_DETAIL_LENGTH = 2000


class FailureKind(StrEnum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    DEFECT = "defect"


class FailureCode:
    """Codes recorded for outcomes that are not raised as a single exception.

    Codes raised as `DocumentProcessingError` subclasses live on those classes;
    these are the ones the lifecycle itself decides.
    """

    #: A transient failure on the final permitted attempt.
    RETRIES_EXHAUSTED = "PROCESSING_RETRIES_EXHAUSTED"
    #: A worker stopped holding the job without recording an outcome -- killed,
    #: restarted, out of memory. Transient on its own; see `INTERRUPTED`.
    WORKER_LOST = "WORKER_LOST"
    #: Worker loss on the final permitted attempt. A document that kills the
    #: worker every time must not be retried forever.
    INTERRUPTED = "PROCESSING_INTERRUPTED"
    #: An unclassified exception.
    INTERNAL = "INTERNAL_PROCESSING_ERROR"
    #: A job/version combination that no correct code path produces.
    INCONSISTENT_STATE = "JOB_STATE_INCONSISTENT"


_TRANSIENT_EXHAUSTED_MESSAGE = (
    "ORBIT could not process this document because a service it depends on was "
    "unavailable. Nothing is wrong with the file; try processing it again later."
)
_INTERRUPTED_MESSAGE = (
    "Processing this document was interrupted repeatedly and did not complete. "
    "Try again later; if it keeps happening, the file may be too complex to process."
)
_DEFECT_MESSAGE = (
    "An unexpected error occurred while processing this document. The problem has been logged."
)


@dataclass(frozen=True, slots=True)
class ProcessingFailure:
    code: str
    kind: FailureKind
    #: Safe to show the uploader. Never contains a stack trace, a path, SQL, or
    #: another tenant's anything.
    user_message: str
    #: For operators. May contain exception types and messages; never document
    #: text (the parsers do not put content into exception messages).
    detail: str

    def __post_init__(self) -> None:
        if not self.code:
            msg = "A failure must carry a code."
            raise ValueError(msg)

    @property
    def is_retryable(self) -> bool:
        return self.kind is FailureKind.TRANSIENT

    def bounded_detail(self) -> str:
        return self.detail[:MAX_FAILURE_DETAIL_LENGTH]

    @classmethod
    def permanent(cls, error: DocumentProcessingError) -> ProcessingFailure:
        return cls(
            code=error.code,
            kind=FailureKind.PERMANENT,
            user_message=error.message,
            detail=describe_exception(error),
        )

    @classmethod
    def transient(cls, *, code: str, detail: str) -> ProcessingFailure:
        return cls(
            code=code,
            kind=FailureKind.TRANSIENT,
            user_message=_TRANSIENT_EXHAUSTED_MESSAGE,
            detail=detail,
        )

    @classmethod
    def defect(cls, *, detail: str, code: str = FailureCode.INTERNAL) -> ProcessingFailure:
        return cls(code=code, kind=FailureKind.DEFECT, user_message=_DEFECT_MESSAGE, detail=detail)

    @classmethod
    def worker_lost(cls, *, detail: str) -> ProcessingFailure:
        return cls(
            code=FailureCode.WORKER_LOST,
            kind=FailureKind.TRANSIENT,
            user_message=_INTERRUPTED_MESSAGE,
            detail=detail,
        )

    def exhausted(self) -> ProcessingFailure:
        """The terminal failure recorded on the *version* when a transient
        failure has used its last attempt.

        The job keeps its own specific code; the version gets one that tells
        the user the file was not at fault.
        """
        code = (
            FailureCode.INTERRUPTED
            if self.code == FailureCode.WORKER_LOST
            else FailureCode.RETRIES_EXHAUSTED
        )
        message = (
            _INTERRUPTED_MESSAGE
            if self.code == FailureCode.WORKER_LOST
            else _TRANSIENT_EXHAUSTED_MESSAGE
        )
        return ProcessingFailure(
            code=code, kind=FailureKind.PERMANENT, user_message=message, detail=self.detail
        )


def describe_exception(exc: BaseException) -> str:
    """Type and message, bounded. Deliberately not the traceback: that goes to
    the log record via `logger.exception`, where it belongs."""
    text = f"{type(exc).__module__}.{type(exc).__qualname__}: {exc}"
    return text[:MAX_FAILURE_DETAIL_LENGTH]


def classify_orbit_error(exc: OrbitError) -> ProcessingFailure:
    """Classify an error ORBIT raised deliberately.

    Foreign exceptions -- driver errors, SDK errors, the task runtime's time
    limit -- are the infrastructure classifier's job; this function only knows
    the domain taxonomy, which is why it can be pure.
    """
    if isinstance(exc, DocumentProcessingError):
        return ProcessingFailure.permanent(exc)
    if exc.retryable:
        return ProcessingFailure.transient(code=exc.code, detail=describe_exception(exc))
    # Any other deliberate error inside the pipeline -- a conflict, a
    # not-found on a row the claim just locked -- is a bug, not a property of
    # the document.
    return ProcessingFailure.defect(detail=describe_exception(exc))
