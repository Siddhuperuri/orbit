"""Job lifecycle: claiming, heartbeats, and recording outcomes.

Everything that changes a job's or a version's state lives here, so the
pipeline (`process_document.py`), recovery (`recover_stalled_jobs.py`), and the
worker's time-limit handler all make the same transitions the same way. Each
public method is exactly one transaction, and messages are published only
after that transaction commits -- a message for a row that was rolled back
would be a doorbell for an empty house.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from orbit.application.processing.dispatch import enqueue_after_commit
from orbit.application.processing.policy import ProcessingPolicy
from orbit.core.clock import Clock
from orbit.core.logging import get_logger
from orbit.core.metrics import (
    DOCUMENT_READY_LATENCY,
    JOB_QUEUE_WAIT,
    JOB_RECOVERIES,
    PROCESSING_FAILURES,
)
from orbit.domain.access import SystemContext
from orbit.domain.embeddings import EmbeddingSpace
from orbit.domain.errors import DocumentVersionSupersededError
from orbit.domain.models.entities import DocumentVersion, ProcessingOutcome, ProcessingStatus
from orbit.domain.ports.processing import JobDispatch, ProcessingJobQueue
from orbit.domain.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory
from orbit.domain.processing.content import EmbeddedChunk
from orbit.domain.processing.failures import FailureCode, FailureKind, ProcessingFailure
from orbit.domain.processing.jobs import (
    ClaimDecision,
    ClaimSnapshot,
    JobStatus,
    PipelineStage,
    ProcessingJob,
    decide_claim,
)

logger = get_logger(__name__)

#: The authority every worker-side repository call carries. Named, so
#: privileged cross-tenant access is visible in review and greppable.
PROCESSING_SYSTEM = SystemContext(reason="document-processing")

_ACTIVE_VERSION_STATES = frozenset({ProcessingStatus.PENDING, ProcessingStatus.PROCESSING})

SUPERSEDED_MESSAGE = (
    "This version was replaced by a newer upload, or the document was deleted, "
    "before processing finished."
)


class JobOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    #: Nothing to do: already finished, held elsewhere, not yet due, unknown.
    SKIPPED = "skipped"
    #: This execution lost its lease mid-run; its successor owns the outcome.
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True, slots=True)
class JobRunResult:
    """What one execution did. Returned to the task runtime for its log line,
    and asserted on by tests."""

    job_id: uuid.UUID
    outcome: JobOutcome
    #: A claim decision for skips; a failure code for failures.
    reason: str | None = None
    chunk_count: int | None = None
    next_job_id: uuid.UUID | None = None
    retry_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    job: ProcessingJob
    version: DocumentVersion
    worker_id: str


class LeaseLostError(Exception):
    """This execution no longer holds its job.

    Internal control flow, deliberately not an `OrbitError`: it is not a
    failure of the document or of a dependency, and must never be classified
    and recorded -- recording it is precisely the write fencing forbids.
    """


class JobLifecycle:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        queue: ProcessingJobQueue,
        clock: Clock,
        policy: ProcessingPolicy,
    ) -> None:
        self._uow_factory = uow_factory
        self._queue = queue
        self._clock = clock
        self._policy = policy

    # -- claiming ------------------------------------------------------------

    async def claim(self, job_id: uuid.UUID, *, worker_id: str) -> ClaimedJob | JobRunResult:
        now = self._clock.now()
        dispatches: list[JobDispatch] = []
        async with self._uow_factory() as uow:
            snapshot = await uow.processing.lock_for_claim(PROCESSING_SYSTEM, job_id)
            if snapshot is None:
                exists = await uow.processing.job_exists(PROCESSING_SYSTEM, job_id)
                reason = "locked_by_another_transaction" if exists else "unknown_job"
                logger.info("job.claim_skipped", reason=reason)
                return JobRunResult(job_id=job_id, outcome=JobOutcome.SKIPPED, reason=reason)

            decision = decide_claim(
                snapshot, now=now, early_tolerance=self._policy.early_claim_tolerance
            )
            match decision:
                case ClaimDecision.PROCEED:
                    claimed = await self._start(uow, snapshot, worker_id=worker_id, now=now)
                    await uow.commit()
                    # How long the job waited *after it was due*: a retry
                    # backoff is not queueing, so it is not counted as wait.
                    JOB_QUEUE_WAIT.observe(
                        max((now - snapshot.job.scheduled_for).total_seconds(), 0)
                    )
                    logger.info(
                        "job.claimed",
                        attempt=claimed.job.attempt,
                        run_attempt=claimed.job.run_attempt,
                        content_type=claimed.version.content_type,
                        byte_size=claimed.version.byte_size,
                        lease_expires_at=_iso(claimed.job.lease_expires_at),
                    )
                    return claimed
                case ClaimDecision.ABANDONED:
                    result = await self._abandon_locked(uow, snapshot, now=now, out=dispatches)
                    await uow.commit()
                case ClaimDecision.SUPERSEDED:
                    result = await self._close_superseded(uow, snapshot, now=now)
                    await uow.commit()
                case ClaimDecision.INCONSISTENT:
                    result = await self._close_inconsistent(uow, snapshot, now=now)
                    await uow.commit()
                case (
                    ClaimDecision.ALREADY_FINISHED
                    | ClaimDecision.HELD_ELSEWHERE
                    | ClaimDecision.NOT_DUE
                ):
                    logger.info(
                        "job.claim_skipped",
                        reason=decision.value,
                        job_status=snapshot.job.status.value,
                        scheduled_for=_iso(snapshot.job.scheduled_for),
                    )
                    return JobRunResult(
                        job_id=job_id, outcome=JobOutcome.SKIPPED, reason=decision.value
                    )

        await self._publish(dispatches)
        return result

    async def _start(
        self, uow: UnitOfWork, snapshot: ClaimSnapshot, *, worker_id: str, now: datetime
    ) -> ClaimedJob:
        lease_expires_at = now + self._policy.lease
        await uow.processing.mark_running(
            PROCESSING_SYSTEM,
            snapshot.job.id,
            worker_id=worker_id,
            lease_expires_at=lease_expires_at,
        )
        moved = await uow.processing.transition_version(
            PROCESSING_SYSTEM,
            snapshot.version.id,
            expected=_ACTIVE_VERSION_STATES,
            outcome=ProcessingOutcome(status=ProcessingStatus.PROCESSING),
        )
        if not moved:  # pragma: no cover -- decide_claim checked this under the same lock
            msg = "Version left the processable states while locked."
            raise RuntimeError(msg)
        return ClaimedJob(
            job=replace(
                snapshot.job,
                status=JobStatus.RUNNING,
                worker_id=worker_id,
                lease_expires_at=lease_expires_at,
                stage=PipelineStage.CLAIMED,
            ),
            version=replace(snapshot.version, status=ProcessingStatus.PROCESSING),
            worker_id=worker_id,
        )

    # -- while running ---------------------------------------------------------

    async def heartbeat(self, claimed: ClaimedJob, stage: PipelineStage) -> None:
        """Renew the lease and record the stage being entered.

        Raises `LeaseLostError` if recovery has already taken the job: the
        caller must stop without writing anything further.
        """
        async with self._uow_factory() as uow:
            renewed = await uow.processing.renew_lease(
                PROCESSING_SYSTEM,
                claimed.job.id,
                worker_id=claimed.worker_id,
                lease_expires_at=self._clock.now() + self._policy.lease,
                stage=stage,
            )
            if not renewed:
                raise LeaseLostError
            await uow.commit()

    # -- outcomes --------------------------------------------------------------

    async def complete(
        self,
        claimed: ClaimedJob,
        chunks: Sequence[EmbeddedChunk],
        *,
        page_count: int | None,
        space: EmbeddingSpace,
        chunker_version: str,
    ) -> JobRunResult:
        """The index transaction: chunks, READY, and SUCCEEDED, atomically.

        READY is the last write, and only after the index is *verified* in the
        same transaction: every chunk of the version is present with a vector
        in `space`. The insert makes that true by construction; the count
        makes it a checked fact, so no later change to how chunks are written
        can let a version become READY with an incomplete index.

        The fenced job update runs first. It both proves this execution still
        holds the lease and takes the job's row lock before the version's --
        the same order a claim takes them, so the two cannot deadlock.
        """
        async with self._uow_factory() as uow:
            if not await uow.processing.complete_job(
                PROCESSING_SYSTEM, claimed.job.id, worker_id=claimed.worker_id
            ):
                raise LeaseLostError

            locked = await uow.processing.lock_version_for_index(
                PROCESSING_SYSTEM, claimed.version.id
            )
            if locked is None or locked[1] or not locked[0].is_current:
                # Raised inside the transaction, so the job completion above is
                # rolled back and the failure path can record this attempt.
                raise DocumentVersionSupersededError(SUPERSEDED_MESSAGE)
            version = locked[0]

            indexed = await uow.processing.replace_chunks(
                PROCESSING_SYSTEM,
                version,
                chunks,
                space=space,
                chunker_version=chunker_version,
            )
            indexed_in_space = await uow.embeddings.count_indexed(
                PROCESSING_SYSTEM, version.id, space=space
            )
            if not indexed.chunk_count == indexed_in_space == len(chunks):
                msg = (
                    f"Index verification failed: {len(chunks)} chunks written, "
                    f"{indexed_in_space} indexed in {space.key}."
                )
                raise RuntimeError(msg)
            ready = await uow.processing.transition_version(
                PROCESSING_SYSTEM,
                version.id,
                expected=frozenset({ProcessingStatus.PROCESSING}),
                outcome=ProcessingOutcome.ready(
                    chunk_count=indexed.chunk_count, page_count=page_count
                ),
            )
            if not ready:
                msg = f"Version was {version.status.value}, not processing, at index time."
                raise RuntimeError(msg)
            await uow.commit()

        # `attempt == run_attempt` only for a document's first-ever run. A
        # manual reprocess days later would otherwise record days of "latency"
        # and bury the number this exists to show: what an upload waits for.
        if claimed.job.attempt == claimed.job.run_attempt:
            DOCUMENT_READY_LATENCY.observe(
                max((self._clock.now() - claimed.version.created_at).total_seconds(), 0)
            )
        return JobRunResult(
            job_id=claimed.job.id, outcome=JobOutcome.SUCCEEDED, chunk_count=indexed.chunk_count
        )

    async def record_failure(self, claimed: ClaimedJob, failure: ProcessingFailure) -> JobRunResult:
        """Record a failed attempt, fenced, and schedule a retry if one is due."""
        now = self._clock.now()
        dispatches: list[JobDispatch] = []
        async with self._uow_factory() as uow:
            recorded = await uow.processing.fail_job(
                PROCESSING_SYSTEM,
                claimed.job.id,
                failure=failure,
                worker_id=claimed.worker_id,
                now=now,
            )
            if not recorded:
                logger.warning("job.lease_lost", phase="record_failure", error_code=failure.code)
                return JobRunResult(job_id=claimed.job.id, outcome=JobOutcome.LEASE_LOST)
            result = await self._retry_or_fail(uow, claimed.job, failure, now=now, out=dispatches)
            await uow.commit()
        await self._publish(dispatches)
        return result

    async def record_failure_by_id(
        self, job_id: uuid.UUID, *, worker_id: str, failure: ProcessingFailure
    ) -> JobRunResult:
        """`record_failure` for a caller that no longer has the claim in hand
        -- the task runtime, after a time limit unwound the pipeline."""
        now = self._clock.now()
        dispatches: list[JobDispatch] = []
        async with self._uow_factory() as uow:
            snapshot = await uow.processing.lock_for_claim(PROCESSING_SYSTEM, job_id)
            if (
                snapshot is None
                or snapshot.job.status is not JobStatus.RUNNING
                or snapshot.job.worker_id != worker_id
            ):
                logger.warning(
                    "job.lease_lost", phase="record_failure_by_id", error_code=failure.code
                )
                return JobRunResult(job_id=job_id, outcome=JobOutcome.LEASE_LOST)
            await uow.processing.fail_job(
                PROCESSING_SYSTEM, job_id, failure=failure, worker_id=worker_id, now=now
            )
            result = await self._retry_or_fail(uow, snapshot.job, failure, now=now, out=dispatches)
            await uow.commit()
        await self._publish(dispatches)
        return result

    async def abandon(self, job_id: uuid.UUID) -> JobRunResult:
        """Recovery's entry point for a job whose lease expired."""
        now = self._clock.now()
        dispatches: list[JobDispatch] = []
        async with self._uow_factory() as uow:
            snapshot = await uow.processing.lock_for_claim(PROCESSING_SYSTEM, job_id)
            if snapshot is None:
                return JobRunResult(
                    job_id=job_id, outcome=JobOutcome.SKIPPED, reason="locked_or_missing"
                )
            decision = decide_claim(
                snapshot, now=now, early_tolerance=self._policy.early_claim_tolerance
            )
            if decision is not ClaimDecision.ABANDONED:
                return JobRunResult(
                    job_id=job_id, outcome=JobOutcome.SKIPPED, reason=decision.value
                )
            result = await self._abandon_locked(uow, snapshot, now=now, out=dispatches)
            await uow.commit()
        await self._publish(dispatches)
        return result

    async def publish_undelivered(self) -> int:
        """Re-publish queued jobs whose message is presumed lost."""
        now = self._clock.now()
        async with self._uow_factory() as uow:
            jobs = await uow.processing.find_undelivered(
                PROCESSING_SYSTEM,
                stale_before=now - self._policy.redelivery_grace,
                limit=self._policy.recovery_batch_size,
            )
            await uow.processing.mark_enqueued(PROCESSING_SYSTEM, [job.id for job in jobs], at=now)
            await uow.commit()
        if jobs:
            JOB_RECOVERIES.labels(kind="redelivered").inc(len(jobs))
        for job in jobs:
            logger.warning(
                "job.redelivered",
                job_id=str(job.id),
                scheduled_for=_iso(job.scheduled_for),
                last_enqueued_at=_iso(job.enqueued_at),
            )
        await self._publish([JobDispatch(job_id=job.id, request_id=job.request_id) for job in jobs])
        return len(jobs)

    # -- shared transitions ----------------------------------------------------

    async def _abandon_locked(
        self,
        uow: UnitOfWork,
        snapshot: ClaimSnapshot,
        *,
        now: datetime,
        out: list[JobDispatch],
    ) -> JobRunResult:
        job = snapshot.job
        failure = ProcessingFailure.worker_lost(
            detail=(
                f"Lease held by {job.worker_id} expired at "
                f"{job.lease_expires_at.isoformat() if job.lease_expires_at else 'unknown'} "
                f"during stage {job.stage.value if job.stage else 'unknown'}."
            )
        )
        recorded = await uow.processing.fail_job(
            PROCESSING_SYSTEM, job.id, failure=failure, worker_id=None, now=now
        )
        if not recorded:  # pragma: no cover -- guarded by the lock and decide_claim
            return JobRunResult(job_id=job.id, outcome=JobOutcome.SKIPPED, reason="not_abandoned")
        JOB_RECOVERIES.labels(kind="abandoned").inc()
        logger.warning(
            "job.abandoned",
            job_id=str(job.id),
            previous_worker_id=job.worker_id,
            stage=job.stage.value if job.stage else None,
            lease_expires_at=_iso(job.lease_expires_at),
            run_attempt=job.run_attempt,
        )
        if snapshot.document_deleted or not snapshot.version.is_current:
            await self._fail_version(uow, job, _superseded_failure())
            return JobRunResult(
                job_id=job.id,
                outcome=JobOutcome.FAILED,
                reason=DocumentVersionSupersededError.code,
            )
        return await self._retry_or_fail(uow, job, failure, now=now, out=out)

    async def _close_superseded(
        self, uow: UnitOfWork, snapshot: ClaimSnapshot, *, now: datetime
    ) -> JobRunResult:
        failure = _superseded_failure()
        await uow.processing.fail_job(
            PROCESSING_SYSTEM, snapshot.job.id, failure=failure, worker_id=None, now=now
        )
        await self._fail_version(uow, snapshot.job, failure)
        logger.info("job.superseded", document_deleted=snapshot.document_deleted)
        return JobRunResult(job_id=snapshot.job.id, outcome=JobOutcome.FAILED, reason=failure.code)

    async def _close_inconsistent(
        self, uow: UnitOfWork, snapshot: ClaimSnapshot, *, now: datetime
    ) -> JobRunResult:
        failure = ProcessingFailure.defect(
            code=FailureCode.INCONSISTENT_STATE,
            detail=(
                f"Queued job for a version already {snapshot.version.status.value}; "
                "the version was left unchanged."
            ),
        )
        await uow.processing.fail_job(
            PROCESSING_SYSTEM, snapshot.job.id, failure=failure, worker_id=None, now=now
        )
        PROCESSING_FAILURES.labels(
            error_code=failure.code, failure_kind=failure.kind.value, terminal="true"
        ).inc()
        # The version is deliberately not touched: it is terminal, and nothing
        # about this orphaned job says its recorded outcome is wrong.
        logger.error("job.inconsistent_state", version_status=snapshot.version.status.value)
        return JobRunResult(job_id=snapshot.job.id, outcome=JobOutcome.FAILED, reason=failure.code)

    async def _retry_or_fail(
        self,
        uow: UnitOfWork,
        job: ProcessingJob,
        failure: ProcessingFailure,
        *,
        now: datetime,
        out: list[JobDispatch],
    ) -> JobRunResult:
        retry = self._policy.retry
        if retry.should_retry(failure, run_attempt=job.run_attempt):
            delay = retry.delay_before(job.run_attempt + 1, random=self._policy.random)
            next_job = await uow.processing.schedule_retry(
                PROCESSING_SYSTEM, job, scheduled_for=now + delay
            )
            await uow.processing.transition_version(
                PROCESSING_SYSTEM,
                job.document_version_id,
                expected=_ACTIVE_VERSION_STATES,
                # Back to PENDING: the document is fine, a dependency is not.
                outcome=ProcessingOutcome(status=ProcessingStatus.PENDING),
            )
            out.append(JobDispatch(job_id=next_job.id, request_id=job.request_id, delay=delay))
            PROCESSING_FAILURES.labels(
                error_code=failure.code, failure_kind=failure.kind.value, terminal="false"
            ).inc()
            logger.warning(
                "job.retry_scheduled",
                error_code=failure.code,
                failure_kind=failure.kind.value,
                run_attempt=job.run_attempt,
                next_run_attempt=next_job.run_attempt,
                max_attempts=retry.max_attempts,
                delay_seconds=round(delay.total_seconds(), 1),
                next_job_id=str(next_job.id),
            )
            return JobRunResult(
                job_id=job.id,
                outcome=JobOutcome.RETRY_SCHEDULED,
                reason=failure.code,
                next_job_id=next_job.id,
                retry_at=next_job.scheduled_for,
            )

        terminal = failure.exhausted() if failure.kind is FailureKind.TRANSIENT else failure
        await self._fail_version(uow, job, terminal)
        PROCESSING_FAILURES.labels(
            error_code=failure.code, failure_kind=failure.kind.value, terminal="true"
        ).inc()
        log = logger.error if terminal.kind is FailureKind.DEFECT else logger.warning
        log(
            "job.failed_permanently",
            error_code=failure.code,
            version_failure_code=terminal.code,
            failure_kind=failure.kind.value,
            run_attempt=job.run_attempt,
            retries_exhausted=failure.kind is FailureKind.TRANSIENT,
        )
        return JobRunResult(job_id=job.id, outcome=JobOutcome.FAILED, reason=terminal.code)

    async def _fail_version(
        self, uow: UnitOfWork, job: ProcessingJob, failure: ProcessingFailure
    ) -> None:
        await uow.processing.transition_version(
            PROCESSING_SYSTEM,
            job.document_version_id,
            expected=_ACTIVE_VERSION_STATES,
            outcome=ProcessingOutcome.failed(code=failure.code, reason=failure.user_message),
        )

    async def _publish(self, dispatches: Sequence[JobDispatch]) -> None:
        for dispatch in dispatches:
            await enqueue_after_commit(self._queue, dispatch)


def _superseded_failure() -> ProcessingFailure:
    return ProcessingFailure.permanent(DocumentVersionSupersededError(SUPERSEDED_MESSAGE))


def _iso(value: datetime | None) -> str | None:
    """Log fields are strings: a datetime repr is unqueryable in a log store."""
    return value.isoformat() if value else None
