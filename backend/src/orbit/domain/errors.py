"""Domain error hierarchy.

Errors are raised here with a stable machine-readable ``code``. The HTTP layer
maps them to responses; nothing in this module imports anything web-related, so
use cases stay testable without a framework.

``http_status`` is advisory metadata that travels with the error, not a coupling
to HTTP. It lives beside the error so that adding a new error type cannot
silently fall through to 500 -- the mapping layer asserts every subclass
declares one.

See docs/decisions/0014-error-handling-strategy.md.
"""

from __future__ import annotations

from typing import ClassVar

# Status codes are written as literals rather than imported from a web framework:
# `domain` must not depend on HTTP machinery, and these five numbers are stable.
_HTTP_BAD_REQUEST = 400
_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403
_HTTP_NOT_FOUND = 404
_HTTP_CONFLICT = 409
_HTTP_PAYLOAD_TOO_LARGE = 413
_HTTP_UNSUPPORTED_MEDIA_TYPE = 415
_HTTP_UNPROCESSABLE = 422
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_INTERNAL = 500
_HTTP_SERVICE_UNAVAILABLE = 503


class OrbitError(Exception):
    """Base class for every error ORBIT raises deliberately.

    Anything that is not an ``OrbitError`` reaching the HTTP boundary is a
    defect: it becomes a 500 with a generic message, and the full detail is
    logged rather than returned.
    """

    #: Stable identifier. Part of the public API contract -- never repurposed.
    code: ClassVar[str] = "INTERNAL_ERROR"
    #: Advisory HTTP status for the mapping layer.
    http_status: ClassVar[int] = _HTTP_INTERNAL
    #: Whether the same request could succeed if repeated later.
    retryable: ClassVar[bool] = False

    def __init__(self, message: str, /, **context: object) -> None:
        super().__init__(message)
        self.message = message
        #: Structured detail for logs. Deliberately NOT serialised into the
        #: response body -- it exists for the operator, not the caller.
        self.context = context

    def __str__(self) -> str:
        return self.message


# --------------------------------------------------------------------------
# Client errors -- the request is wrong; repeating it unchanged will not help.
# --------------------------------------------------------------------------


class ValidationError(OrbitError):
    code = "VALIDATION_ERROR"
    http_status = _HTTP_UNPROCESSABLE


class NotFoundError(OrbitError):
    """A resource does not exist, or the caller may not know that it does.

    Requests for a resource in an inaccessible workspace also raise this rather
    than a permission error: a 403 would confirm the resource exists, leaking
    membership and document existence across tenants.
    """

    code = "NOT_FOUND"
    http_status = _HTTP_NOT_FOUND


class ConflictError(OrbitError):
    code = "CONFLICT"
    http_status = _HTTP_CONFLICT


class AuthenticationRequiredError(OrbitError):
    code = "AUTHENTICATION_REQUIRED"
    http_status = _HTTP_UNAUTHORIZED


class InvalidCredentialsError(OrbitError):
    """Deliberately identical for unknown account and wrong password.

    Distinguishing them would turn the login endpoint into an account
    enumeration oracle.
    """

    code = "INVALID_CREDENTIALS"
    http_status = _HTTP_UNAUTHORIZED


class PermissionDeniedError(OrbitError):
    """The caller provably has workspace access but lacks this permission.

    Only raised where the caller already knows the resource exists; otherwise
    ``NotFoundError`` is correct.
    """

    code = "PERMISSION_DENIED"
    http_status = _HTTP_FORBIDDEN


class BadRequestError(OrbitError):
    code = "BAD_REQUEST"
    http_status = _HTTP_BAD_REQUEST


class UploadTooLargeError(OrbitError):
    code = "UPLOAD_TOO_LARGE"
    http_status = _HTTP_PAYLOAD_TOO_LARGE


class UnsupportedContentTypeError(OrbitError):
    code = "UNSUPPORTED_CONTENT_TYPE"
    http_status = _HTTP_UNSUPPORTED_MEDIA_TYPE


class RateLimitedError(OrbitError):
    code = "RATE_LIMITED"
    http_status = _HTTP_TOO_MANY_REQUESTS
    retryable = True

    @property
    def retry_after_seconds(self) -> int | None:
        """How long to wait, if the raiser knew.

        Read from `context` rather than taken as a constructor argument so the
        signature stays identical to every other error; the HTTP layer turns
        it into a `Retry-After` header. Without this a client's only strategy
        is to retry blindly, which is how a rate limit becomes a hot loop.
        """
        value = self.context.get("retry_after_seconds")
        return value if isinstance(value, int) else None


# --------------------------------------------------------------------------
# Transient dependency failures -- the request is fine; the dependency is not.
#
# These share a taxonomy with the worker's retry policy: what the API reports as
# retryable is exactly what the queue retries with backoff, so a provider outage
# behaves consistently in both. See docs/architecture/data-flow.md.
# --------------------------------------------------------------------------


class DependencyUnavailableError(OrbitError):
    code = "DEPENDENCY_UNAVAILABLE"
    http_status = _HTTP_SERVICE_UNAVAILABLE
    retryable = True


class StorageUnavailableError(DependencyUnavailableError):
    code = "STORAGE_UNAVAILABLE"


class DatabaseUnavailableError(DependencyUnavailableError):
    code = "DATABASE_UNAVAILABLE"


class QueueUnavailableError(DependencyUnavailableError):
    code = "QUEUE_UNAVAILABLE"


class AIProviderUnavailableError(DependencyUnavailableError):
    code = "AI_PROVIDER_UNAVAILABLE"


class AIProviderResponseInvalidError(AIProviderUnavailableError):
    """The provider answered, but with something that cannot be used -- the
    wrong number of vectors, a NaN, a zero vector. Retryable: it is a fault in
    the provider, not in the caller's input."""

    code = "AI_PROVIDER_RESPONSE_INVALID"


class AIProviderTimeoutError(AIProviderUnavailableError):
    """The provider did not answer -- or stopped answering mid-stream --
    within the allotted time."""

    code = "AI_PROVIDER_TIMEOUT"


class AIProviderRateLimitedError(AIProviderUnavailableError):
    """The provider refused the request for rate. May carry
    `retry_after_seconds` in its context when the provider said how long."""

    code = "AI_PROVIDER_RATE_LIMITED"


class AIProviderCircuitOpenError(AIProviderUnavailableError):
    """Recent calls failed so consistently that this one was not attempted.

    Failing fast is the point: a request queued behind a dead provider holds a
    connection and a user's attention for the full timeout, and then fails
    anyway.
    """

    code = "AI_PROVIDER_CIRCUIT_OPEN"


# --------------------------------------------------------------------------
# Question answering -- which *stage* failed (docs/decisions/0022).
#
# A grounded answer has two dependencies that fail independently and mean
# different things. "We could not search your documents" and "we found the
# sources but the model did not answer" need different messages, different
# alerts, and different runbooks, so they never share a code. The provider
# error that caused either is chained as `__cause__` and logged, never
# returned.
# --------------------------------------------------------------------------


class RetrievalFailedError(DependencyUnavailableError):
    """Evidence could not be retrieved; no answer was attempted."""

    code = "RETRIEVAL_FAILED"


class GenerationFailedError(DependencyUnavailableError):
    """Evidence was retrieved, but the language model did not produce a
    usable answer."""

    code = "GENERATION_FAILED"


class GenerationTimeoutError(GenerationFailedError):
    code = "GENERATION_TIMEOUT"


class GenerationRateLimitedError(GenerationFailedError):
    """The provider is rate limiting ORBIT. Surfaced as "busy, retry", with
    `Retry-After` when the provider supplied one."""

    code = "GENERATION_RATE_LIMITED"


class ConversationUnavailableError(DependencyUnavailableError):
    """The conversation itself could not be read or written -- the question
    was not recorded, or a produced answer could not be saved."""

    code = "CONVERSATION_UNAVAILABLE"


class FolderNotEmptyError(ConflictError):
    """The folder still holds documents or subfolders, so deleting it would
    either destroy that organisation or silently move it somewhere the user did
    not choose. Refusing is the one outcome that surprises nobody.

    Its own code so a client can explain *what* is in the way (the counts
    travel in `context` for the log, and the message states them for the
    user) instead of showing a generic "that's changed".
    """

    code = "FOLDER_NOT_EMPTY"


class AnswerInProgressError(ConflictError):
    """The conversation already has a question being answered.

    Turns are strictly ordered; a second question while the first is still
    generating would interleave two answers into one thread.
    """

    code = "ANSWER_IN_PROGRESS"


class StoredObjectNotFoundError(NotFoundError):
    """A storage key that a database row references holds no object.

    Distinct from `StorageUnavailableError` on purpose. "Storage is down"
    is transient and worth retrying; "the object is not there" will be just
    as true in ten minutes, and retrying it only delays telling the user.
    """

    code = "STORED_OBJECT_NOT_FOUND"


# --------------------------------------------------------------------------
# Document processing -- the document is the problem.
#
# Raised only inside the worker. The *message* of each is written for the
# person who uploaded the file and is stored as the version's
# `failure_reason`; the operator detail travels in `context` and reaches logs
# and the job row, never the user. Every one of these is permanent: retrying
# cannot change the bytes that caused it (docs/architecture/data-flow.md).
# --------------------------------------------------------------------------


class DocumentProcessingError(OrbitError):
    code = "DOCUMENT_PROCESSING_FAILED"
    http_status = _HTTP_UNPROCESSABLE


class DocumentEmptyError(DocumentProcessingError):
    code = "DOCUMENT_EMPTY"


class DocumentNoExtractableTextError(DocumentProcessingError):
    """A PDF with pages but no text layer -- almost always a scan.

    Its own code rather than `DOCUMENT_EMPTY`, because the remedy differs: the
    file is not empty, ORBIT simply cannot read images yet (ADR-0012).
    """

    code = "DOCUMENT_NO_EXTRACTABLE_TEXT"


class DocumentCorruptError(DocumentProcessingError):
    code = "DOCUMENT_CORRUPT"


class DocumentEncryptedError(DocumentProcessingError):
    code = "DOCUMENT_ENCRYPTED"


class DocumentEncodingError(DocumentProcessingError):
    code = "DOCUMENT_ENCODING_UNSUPPORTED"


class DocumentLimitExceededError(DocumentProcessingError):
    """Too many pages, characters, blocks, or chunks.

    Limits exist because a hard timeout alone admits a document that produces
    fifty million characters just under the deadline and then breaks the
    embedding stage instead (ADR-0012).
    """

    code = "DOCUMENT_LIMIT_EXCEEDED"


class DocumentFormatUnsupportedError(DocumentProcessingError):
    code = "DOCUMENT_FORMAT_UNSUPPORTED"


class DocumentIntegrityError(DocumentProcessingError):
    """The stored bytes do not hash to what the upload recorded."""

    code = "DOCUMENT_INTEGRITY_FAILED"


class DocumentSourceMissingError(DocumentProcessingError):
    code = "DOCUMENT_SOURCE_MISSING"


class DocumentProcessingTimeoutError(DocumentProcessingError):
    code = "DOCUMENT_PROCESSING_TIMEOUT"


class DocumentVersionSupersededError(DocumentProcessingError):
    """A newer upload replaced this version, or the document was deleted,
    while it was being processed. Indexing it would put text the document no
    longer contains into retrieval."""

    code = "DOCUMENT_VERSION_SUPERSEDED"


# --------------------------------------------------------------------------
# Configuration -- raised at startup, never in a request.
# --------------------------------------------------------------------------


class ConfigurationError(OrbitError):
    """Raised when configuration is internally inconsistent.

    Always fatal at startup. A process that refuses to boot is cheaper to
    diagnose than one that boots and corrupts data slowly -- an embedding
    dimension disagreeing with the schema being the canonical example.
    """

    code = "CONFIGURATION_ERROR"
    http_status = _HTTP_INTERNAL


def all_error_types() -> list[type[OrbitError]]:
    """Every concrete ``OrbitError`` subclass, for exhaustiveness tests.

    The error-mapping test uses this to assert that codes are unique and that no
    error can ship without an explicit status.
    """
    seen: list[type[OrbitError]] = []

    def walk(cls: type[OrbitError]) -> None:
        for sub in cls.__subclasses__():
            seen.append(sub)
            walk(sub)

    walk(OrbitError)
    return seen
