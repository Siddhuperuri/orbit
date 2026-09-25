"""Retry behaviour for any embedding provider, implemented once (ADR-0020).

Two layers of retry exist, and they answer different questions:

* **Here, in-process, seconds.** A 429 or a dropped connection usually clears
  in well under a minute. Retrying the *request* keeps the batches already
  embedded for this document, instead of discarding them and re-running the
  whole job later.
* **The job's durable backoff, minutes** (ADR-0019). If the provider is still
  failing after this layer gives up -- or asks to wait longer than it is
  willing to -- the error propagates, the attempt is recorded as transient,
  the document stays PENDING, and the worker slot is released rather than
  held asleep through an outage.

Only `AIProviderUnavailableError` is retried. A `ConfigurationError` (bad key,
exhausted quota, wrong space) fails the same way every time and propagates at
once.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from orbit.core.logging import get_logger
from orbit.core.metrics import AI_RETRIES
from orbit.domain.embeddings import EmbeddingSpace
from orbit.domain.errors import AIProviderUnavailableError
from orbit.domain.ports.embeddings import EmbeddingProvider
from orbit.infrastructure.ai.telemetry import classify_provider_error

logger = get_logger(__name__)

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class EmbeddingRetryPolicy:
    max_retries: int = 3
    base_delay_seconds: float = 1.0
    #: The most this layer will sleep before one retry. A provider asking for
    #: longer (`Retry-After`) is not waited on here.
    max_wait_seconds: float = 20.0

    def delay_before_retry(self, retry: int, *, random_value: float) -> float:
        """Equal-jitter exponential backoff; `retry` is 1 for the first retry."""
        growth = float(2 ** min(retry - 1, 16))
        ceiling = min(self.max_wait_seconds, self.base_delay_seconds * growth)
        return ceiling / 2 + (ceiling / 2) * min(max(random_value, 0.0), 1.0)


class RetryingEmbeddingProvider:
    def __init__(
        self,
        inner: EmbeddingProvider,
        policy: EmbeddingRetryPolicy,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        self._inner = inner
        self._policy = policy
        self._sleep = sleep
        self._random = random_value

    @property
    def space(self) -> EmbeddingSpace:
        return self._inner.space

    @property
    def max_batch_size(self) -> int:
        return self._inner.max_batch_size

    @property
    def max_batch_tokens(self) -> int:
        return self._inner.max_batch_tokens

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return await self._with_retries(
            lambda: self._inner.embed_documents(texts), operation="documents", inputs=len(texts)
        )

    async def embed_query(self, text: str) -> Sequence[float]:
        return await self._with_retries(
            lambda: self._inner.embed_query(text), operation="query", inputs=1
        )

    async def _with_retries(
        self, call: Callable[[], Awaitable[_T]], *, operation: str, inputs: int
    ) -> _T:
        retry = 0
        while True:
            try:
                return await call()
            except AIProviderUnavailableError as exc:
                retry += 1
                if retry > self._policy.max_retries:
                    logger.warning(
                        "embedding.retries_exhausted",
                        call=operation,
                        inputs=inputs,
                        retries=self._policy.max_retries,
                        error_code=exc.code,
                    )
                    raise
                delay = self._delay(exc, retry)
                if delay is None:
                    logger.warning(
                        "embedding.retry_deferred",
                        call=operation,
                        inputs=inputs,
                        retry_after_seconds=exc.context.get("retry_after_seconds"),
                        max_wait_seconds=self._policy.max_wait_seconds,
                    )
                    raise
                AI_RETRIES.labels(
                    operation=f"embed_{operation}", reason=classify_provider_error(exc)
                ).inc()
                logger.info(
                    "embedding.retrying",
                    call=operation,
                    inputs=inputs,
                    retry=retry,
                    delay_seconds=round(delay, 3),
                    error_code=exc.code,
                    status=exc.context.get("status"),
                )
                await self._sleep(delay)

    def _delay(self, exc: AIProviderUnavailableError, retry: int) -> float | None:
        """How long to wait, or `None` to hand the failure to the job's backoff."""
        requested = exc.context.get("retry_after_seconds")
        if isinstance(requested, (int, float)):
            if requested > self._policy.max_wait_seconds:
                return None
            # Honour the provider's floor, with a little jitter above it so
            # every worker told "1 second" does not return in the same instant.
            return float(requested) + self._random() * self._policy.base_delay_seconds
        return self._policy.delay_before_retry(retry, random_value=self._random())
