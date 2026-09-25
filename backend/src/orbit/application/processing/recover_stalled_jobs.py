"""Recovery: the reason the queue does not have to be reliable.

Two things can strand a job, and this sweep repairs both:

1. **A worker died holding it.** Killed, restarted, out of memory, host lost.
   Its lease expires; the attempt is recorded as `WORKER_LOST` and the next is
   scheduled (or, on the last attempt, the version fails with a reason).
2. **Its message never arrived.** The publish after commit failed, or Redis
   lost it. The job is still `QUEUED` past a grace period; it is re-published.

Runs when a worker starts -- so restarting a crashed worker is itself the
recovery -- and on a schedule. Safe to run concurrently with itself and with
live workers: every step is a conditional, row-locked transition.
"""

from __future__ import annotations

from dataclasses import dataclass

from orbit.application.processing.lifecycle import (
    PROCESSING_SYSTEM,
    JobLifecycle,
    JobOutcome,
)
from orbit.application.processing.policy import ProcessingPolicy
from orbit.core.clock import Clock
from orbit.core.logging import get_logger
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    abandoned_found: int
    abandoned_recovered: int
    redelivered: int


class RecoverStalledJobs:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        lifecycle: JobLifecycle,
        clock: Clock,
        policy: ProcessingPolicy,
    ) -> None:
        self._uow_factory = uow_factory
        self._lifecycle = lifecycle
        self._clock = clock
        self._policy = policy

    async def execute(self) -> RecoveryReport:
        async with self._uow_factory() as uow:
            abandoned = await uow.processing.find_abandoned(
                PROCESSING_SYSTEM,
                now=self._clock.now(),
                limit=self._policy.recovery_batch_size,
            )

        recovered = 0
        for job_id in abandoned:
            # One transaction per job: one job's problem cannot roll back the
            # recovery of the others.
            result = await self._lifecycle.abandon(job_id)
            if result.outcome is not JobOutcome.SKIPPED:
                recovered += 1

        redelivered = await self._lifecycle.publish_undelivered()

        report = RecoveryReport(
            abandoned_found=len(abandoned),
            abandoned_recovered=recovered,
            redelivered=redelivered,
        )
        if abandoned or redelivered:
            logger.warning(
                "recovery.completed",
                abandoned_found=report.abandoned_found,
                abandoned_recovered=report.abandoned_recovered,
                redelivered=report.redelivered,
            )
        else:
            logger.info("recovery.completed", abandoned_found=0, redelivered=0)
        return report
