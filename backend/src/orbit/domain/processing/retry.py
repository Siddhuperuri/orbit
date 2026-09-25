"""Retry policy: how many attempts, and how long to wait between them.

Pure and deterministic given its random source, so the backoff schedule is a
unit-tested value rather than behaviour observed by sleeping.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from orbit.domain.processing.failures import ProcessingFailure


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded, jittered exponential backoff.

    **Bounded**, because a transient failure that never clears must eventually
    become a terminal state a user can see and act on, rather than a document
    that is "pending" forever.

    **Jittered** ("equal jitter": half the ceiling fixed, half random), because
    a provider outage fails every in-flight document at once, and un-jittered
    backoff would bring them all back at the same instant -- a synchronized
    retry storm against a service that has only just recovered.
    """

    max_attempts: int = 5
    base_delay: timedelta = timedelta(seconds=15)
    max_delay: timedelta = timedelta(minutes=15)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            msg = "max_attempts must be at least 1."
            raise ValueError(msg)
        if self.base_delay <= timedelta(0) or self.max_delay < self.base_delay:
            msg = "Require 0 < base_delay <= max_delay."
            raise ValueError(msg)

    def should_retry(self, failure: ProcessingFailure, *, run_attempt: int) -> bool:
        """Only transient failures retry, and only while attempts remain.

        `run_attempt` is the attempt number within the current processing run
        (1-based), not across the version's whole history: an operator
        reprocessing a document starts a fresh budget.
        """
        return failure.is_retryable and run_attempt < self.max_attempts

    def delay_before(self, next_attempt: int, *, random: Callable[[], float]) -> timedelta:
        """How long to wait before attempt `next_attempt` (2 is the first retry).

        `random` returns a float in [0, 1); injected so tests can pin it.
        """
        if next_attempt < 2:  # noqa: PLR2004 -- attempt 1 is never a retry
            msg = "next_attempt must be at least 2."
            raise ValueError(msg)
        exponent = next_attempt - 2
        # Capped before exponentiation grows large, so a misconfigured
        # max_attempts cannot produce an overflow here.
        ceiling_seconds = min(
            self.max_delay.total_seconds(),
            self.base_delay.total_seconds() * (2 ** min(exponent, 32)),
        )
        half = ceiling_seconds / 2
        return timedelta(seconds=half + half * min(max(random(), 0.0), 1.0))
