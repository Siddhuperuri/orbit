"""Reading the job queue's state for the metrics gauges."""

from __future__ import annotations

from orbit.application.processing.lifecycle import PROCESSING_SYSTEM
from orbit.core.clock import Clock
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory
from orbit.domain.processing.jobs import QueueSnapshot


class ReadQueueSnapshot:
    """Counts live jobs by state, from PostgreSQL.

    The database, not the broker, is asked: it is the source of truth for job
    state (ADR-0019), it can distinguish a job waiting for a retry from one
    that is overdue, and it still answers when Redis is down -- which is
    exactly when an operator needs to know what is waiting.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self) -> QueueSnapshot:
        async with self._uow_factory() as uow:
            return await uow.processing.queue_snapshot(PROCESSING_SYSTEM, now=self._clock.now())
