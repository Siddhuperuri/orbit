"""The knobs of the processing pipeline, gathered into one value.

Built from `Settings` by the composition root and injected, so a test can run
the whole pipeline with a one-second lease and no backoff without touching
configuration.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from orbit.core.config import Settings
from orbit.domain.processing.content import ParseLimits
from orbit.domain.processing.retry import RetryPolicy


@dataclass(frozen=True, slots=True)
class ProcessingPolicy:
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    limits: ParseLimits = field(default_factory=ParseLimits)
    #: How long a claim stays valid without renewal. Must exceed the longest
    #: time one stage can run without a heartbeat, or a live worker's job is
    #: recovered out from under it -- which fencing makes *safe* but wasteful.
    lease: timedelta = timedelta(minutes=16)
    #: A message may arrive this much before its job's `scheduled_for` and still
    #: be honoured; broker countdowns are not precise.
    early_claim_tolerance: timedelta = timedelta(seconds=30)
    #: A queued job whose message was last published longer ago than this is
    #: presumed lost and re-published by recovery.
    redelivery_grace: timedelta = timedelta(minutes=10)
    #: Jobs handled per recovery sweep, per category.
    recovery_batch_size: int = 100
    #: Downloaded source above this size spills from memory to a temporary file.
    spool_memory_bytes: int = 8 * 1024 * 1024
    #: Source of jitter for retry delays. Injected so tests are deterministic.
    random: Callable[[], float] = random.random

    @classmethod
    def from_settings(cls, settings: Settings) -> ProcessingPolicy:
        return cls(
            retry=RetryPolicy(
                max_attempts=settings.processing_max_attempts,
                base_delay=timedelta(seconds=settings.processing_retry_base_seconds),
                max_delay=timedelta(seconds=settings.processing_retry_max_seconds),
            ),
            limits=ParseLimits(
                max_pages=settings.processing_max_pages,
                max_characters=settings.processing_max_characters,
                max_chunks=settings.processing_max_chunks,
            ),
            lease=timedelta(seconds=settings.processing_lease_seconds),
            redelivery_grace=timedelta(seconds=settings.processing_redelivery_grace_seconds),
        )
