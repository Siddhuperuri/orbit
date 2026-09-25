"""Retries and circuit breaking for any language-model provider, implemented once.

Chat is interactive, so the policy is the embedding decorator's with a much
shorter fuse: a person is waiting, and a request that retries for a minute has
failed whether or not it eventually succeeds. The use case's own generation
deadline bounds everything here, retries included.

**A stream is retried only before its first event.** Once text has been
handed on it may already be on a user's screen; replaying the request would
duplicate or contradict it. After the first event a failure propagates, and
the use case records whatever was produced as a partial answer.

**The circuit breaker** fails calls fast after consecutive provider failures,
then lets one probe through after a cool-down. Unlike the worker (ADR-0020),
the API is a long-lived process, so the breaker's state is meaningful: without
it, an outage makes every question wait out the full timeout before failing.
Rate limiting does not trip it -- a 429 proves the provider is alive -- and
neither does a cancelled call, which says nothing about the provider.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass

from orbit.core.logging import get_logger
from orbit.core.metrics import AI_RETRIES, LLM_CIRCUIT_OPEN
from orbit.domain.errors import (
    AIProviderCircuitOpenError,
    AIProviderRateLimitedError,
    AIProviderUnavailableError,
)
from orbit.domain.llm import Completion, LLMMessage, StreamEvent
from orbit.domain.ports.llm import LLMProvider
from orbit.infrastructure.ai.telemetry import classify_provider_error

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class LLMRetryPolicy:
    max_retries: int = 2
    base_delay_seconds: float = 0.5
    #: A provider asking to wait longer than this is not waited on: the
    #: failure goes back to the user as "busy, retry" instead.
    max_wait_seconds: float = 4.0

    def delay(
        self, exc: AIProviderUnavailableError, retry: int, random_value: float
    ) -> float | None:
        requested = exc.context.get("retry_after_seconds")
        if isinstance(requested, (int, float)):
            if requested > self.max_wait_seconds:
                return None
            return float(requested) + random_value * self.base_delay_seconds
        # Equal-jitter exponential backoff; `retry` is 1 for the first retry.
        ceiling = min(
            self.max_wait_seconds, self.base_delay_seconds * float(2 ** min(retry - 1, 16))
        )
        return ceiling / 2 + (ceiling / 2) * min(max(random_value, 0.0), 1.0)


class CircuitBreaker:
    """Closed -> open after `failure_threshold` consecutive failures; after
    `reset_after_seconds` one probe is let through (half-open). Its success
    closes the circuit; its failure re-opens it."""

    def __init__(
        self,
        *,
        failure_threshold: int,
        reset_after_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = failure_threshold
        self._reset_after = reset_after_seconds
        self._now = monotonic
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_started_at: float | None = None

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None

    def before_call(self) -> None:
        if self._opened_at is None:
            return
        now = self._now()
        cooled = now - self._opened_at >= self._reset_after
        # A probe that never reported back (its request was cancelled) must
        # not wedge the circuit open forever: it expires like the cool-down.
        probe_free = (
            self._probe_started_at is None or now - self._probe_started_at >= self._reset_after
        )
        if cooled and probe_free:
            self._probe_started_at = now
            return
        msg = "The language model is unavailable; not retrying until it recovers."
        raise AIProviderCircuitOpenError(msg)

    def record_success(self) -> None:
        if self._opened_at is not None:
            logger.info("llm.circuit_closed")
        self._failures = 0
        self._opened_at = None
        self._probe_started_at = None
        LLM_CIRCUIT_OPEN.set(0)

    def record_failure(self, exc: AIProviderUnavailableError) -> None:
        if isinstance(exc, (AIProviderRateLimitedError, AIProviderCircuitOpenError)):
            return
        self._failures += 1
        self._probe_started_at = None
        if self._opened_at is not None or self._failures >= self._threshold:
            if self._opened_at is None:
                logger.warning("llm.circuit_opened", failures=self._failures, error_code=exc.code)
            self._opened_at = self._now()
            LLM_CIRCUIT_OPEN.set(1)


class ResilientLLMProvider:
    def __init__(
        self,
        inner: LLMProvider,
        policy: LLMRetryPolicy,
        breaker: CircuitBreaker,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        self._inner = inner
        self._policy = policy
        self._breaker = breaker
        self._sleep = sleep
        self._random = random_value

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def context_window(self) -> int:
        return self._inner.context_window

    async def complete(
        self, messages: Sequence[LLMMessage], *, max_tokens: int, temperature: float
    ) -> Completion:
        retry = 0
        while True:
            self._breaker.before_call()
            try:
                completion = await self._inner.complete(
                    messages, max_tokens=max_tokens, temperature=temperature
                )
            except AIProviderUnavailableError as exc:
                self._breaker.record_failure(exc)
                retry += 1
                await self._wait_or_raise(exc, retry, operation="complete")
                continue
            self._breaker.record_success()
            return completion

    async def stream(
        self, messages: Sequence[LLMMessage], *, max_tokens: int, temperature: float
    ) -> AsyncIterator[StreamEvent]:
        retry = 0
        while True:
            self._breaker.before_call()
            emitted = False
            try:
                async for event in self._inner.stream(
                    messages, max_tokens=max_tokens, temperature=temperature
                ):
                    emitted = True
                    yield event
            except AIProviderUnavailableError as exc:
                self._breaker.record_failure(exc)
                if emitted:
                    raise
                retry += 1
                await self._wait_or_raise(exc, retry, operation="stream")
                continue
            self._breaker.record_success()
            return

    async def _wait_or_raise(
        self, exc: AIProviderUnavailableError, retry: int, *, operation: str
    ) -> None:
        """Sleep before the next attempt, or re-raise `exc` if there is none."""
        delay = (
            None
            if retry > self._policy.max_retries or isinstance(exc, AIProviderCircuitOpenError)
            else self._policy.delay(exc, retry, self._random())
        )
        if delay is None:
            logger.warning(
                "llm.giving_up",
                call=operation,
                attempts=retry,
                error_code=exc.code,
                retry_after_seconds=exc.context.get("retry_after_seconds"),
            )
            raise exc
        AI_RETRIES.labels(operation=f"chat_{operation}", reason=classify_provider_error(exc)).inc()
        logger.info(
            "llm.retrying",
            call=operation,
            retry=retry,
            delay_seconds=round(delay, 3),
            error_code=exc.code,
            status=exc.context.get("status"),
        )
        await self._sleep(delay)
