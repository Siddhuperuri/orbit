"""Telemetry shared by every adapter that calls an AI provider.

One place decides how a provider call is timed, classified, logged, and
correlated, so the embedding adapter and the language-model adapter cannot
drift apart -- and a third adapter inherits the behaviour by using it.

**Correlation across the process boundary.** ORBIT's ``request_id`` is sent to
the provider as ``X-Client-Request-Id`` (an opaque ULID, safe to disclose), and
the provider's own ``x-request-id`` is captured on failure. A provider support
ticket can therefore quote the provider's id, and an ORBIT log query can be
started from either side.

**What is never recorded:** the prompt, the input text, the completion, or the
API key. A failure carries a status, an error code, and durations -- the body
of an error response may echo the prompt (a document), so it is not kept.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager

import httpx

from orbit.core.logging import current_request_id, get_logger
from orbit.core.metrics import AI_REQUEST_DURATION, AI_TOKENS, Outcome, observe
from orbit.domain.errors import (
    AIProviderCircuitOpenError,
    AIProviderRateLimitedError,
    AIProviderResponseInvalidError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
    ConfigurationError,
    OrbitError,
)

logger = get_logger(__name__)

PROVIDER = "openai"

CLIENT_REQUEST_ID_HEADER = "X-Client-Request-Id"
PROVIDER_REQUEST_ID_HEADER = "x-request-id"

# The provider's id is echoed into logs, so it is bounded: a hostile or broken
# upstream must not be able to write an arbitrarily long field.
_MAX_PROVIDER_REQUEST_ID_CHARS = 128


# Most specific first: rate limiting, timeout, invalid response, and an open
# circuit are all subclasses of `AIProviderUnavailableError`.
_OUTCOMES: tuple[tuple[type[BaseException] | tuple[type[BaseException], ...], str], ...] = (
    ((asyncio.CancelledError, GeneratorExit), "cancelled"),
    (AIProviderCircuitOpenError, "circuit_open"),
    (AIProviderRateLimitedError, "rate_limited"),
    (AIProviderTimeoutError, "timeout"),
    (AIProviderResponseInvalidError, "invalid_response"),
    (AIProviderUnavailableError, "unavailable"),
    (ConfigurationError, "config_error"),
)


def classify_provider_error(exc: BaseException) -> str:
    """A bounded outcome label for a failed provider call."""
    for exception_types, label in _OUTCOMES:
        if isinstance(exc, exception_types):
            return label
    return "error"


def provider_request_id(response: httpx.Response) -> str | None:
    value = response.headers.get(PROVIDER_REQUEST_ID_HEADER)
    return value[:_MAX_PROVIDER_REQUEST_ID_CHARS] if value else None


def correlation_headers() -> dict[str, str]:
    """Headers that let the provider's records be matched to ORBIT's."""
    request_id = current_request_id()
    return {CLIENT_REQUEST_ID_HEADER: request_id} if request_id else {}


def record_tokens(operation: str, *, input_tokens: int | None, output_tokens: int | None) -> None:
    if input_tokens:
        AI_TOKENS.labels(operation=operation, kind="input").inc(input_tokens)
    if output_tokens:
        AI_TOKENS.labels(operation=operation, kind="output").inc(output_tokens)


@contextmanager
def provider_call(*, operation: str, model: str) -> Iterator[Outcome]:
    """Time and log one HTTP call to the provider.

    ``operation`` is one of ``embed_documents``, ``embed_query``,
    ``chat_complete``, ``chat_stream``. Retries (ADR-0020) happen *outside*
    this, so each attempt is its own observation and backoff sleeps are not
    counted as provider latency.
    """
    began = time.perf_counter()
    with observe(
        AI_REQUEST_DURATION,
        classify=classify_provider_error,
        provider=PROVIDER,
        operation=operation,
    ) as outcome:
        try:
            yield outcome
        except Exception as exc:
            context = exc.context if isinstance(exc, OrbitError) else {}
            logger.warning(
                "ai.request_failed",
                call=operation,
                model=model,
                outcome=classify_provider_error(exc),
                error_code=exc.code if isinstance(exc, OrbitError) else type(exc).__name__,
                status=context.get("status"),
                provider_request_id=context.get("provider_request_id"),
                duration_ms=round((time.perf_counter() - began) * 1000, 1),
            )
            raise
        else:
            logger.debug(
                "ai.request_completed",
                call=operation,
                model=model,
                duration_ms=round((time.perf_counter() - began) * 1000, 1),
            )
