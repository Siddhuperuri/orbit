"""Processing jobs, chunks, and pipeline status transitions.

Every method a worker calls on a claimed job is a single conditional statement
whose `WHERE` clause *is* the safety property: "still RUNNING, still held by
me". Checking in Python and then writing would leave a window in which a
recovered job's successor could be overwritten by the presumed-dead original.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import ColumnElement, and_, delete, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from orbit.core.ids import new_uuid7
from orbit.domain.access import AccessContext, SystemContext
from orbit.domain.embeddings import EmbeddingSpace
from orbit.domain.errors import ConflictError, NotFoundError
from orbit.domain.models.entities import DocumentVersion, ProcessingOutcome, ProcessingStatus
from orbit.domain.ports.processing import IndexResult
from orbit.domain.processing.content import EmbeddedChunk
from orbit.domain.processing.failures import FailureKind, ProcessingFailure
from orbit.domain.processing.jobs import (
    ClaimSnapshot,
    JobStatus,
    PipelineStage,
    ProcessingJob,
    QueueSnapshot,
)
from orbit.infrastructure.db.errors import flush_translating_conflicts, translate_integrity_error
from orbit.infrastructure.db.models import Chunk as ChunkRow
from orbit.infrastructure.db.models import Document as DocumentRow
from orbit.infrastructure.db.models import DocumentProcessingJob as JobRow
from orbit.infrastructure.db.models import DocumentVersion as VersionRow
from orbit.infrastructure.db.models import JobStatus as JobStatusRow
from orbit.infrastructure.db.repositories.documents import version_to_entity

#: Rows per INSERT when indexing. Bounded so one statement's parameter payload
#: -- 1536 floats rendered as text per chunk -- stays a few megabytes rather
#: than growing with the document.
_INSERT_BATCH_SIZE = 250


def job_to_entity(row: JobRow) -> ProcessingJob:
    return ProcessingJob(
        id=row.id,
        workspace_id=row.workspace_id,
        document_version_id=row.document_version_id,
        status=JobStatus(row.status.value),
        attempt=row.attempt,
        run_attempt=row.run_attempt,
        scheduled_for=row.scheduled_for,
        created_at=row.created_at,
        enqueued_at=row.enqueued_at,
        request_id=row.request_id,
        worker_id=row.worker_id,
        lease_expires_at=row.lease_expires_at,
        stage=PipelineStage(row.stage) if row.stage else None,
        error_code=row.error_code,
        failure_kind=FailureKind(row.failure_kind.value) if row.failure_kind else None,
        error_message=row.error_message,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


class SqlProcessingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- user-initiated ------------------------------------------------------

    async def create_initial_job(
        self, ctx: AccessContext, version_id: uuid.UUID, *, request_id: str | None
    ) -> ProcessingJob:
        row = JobRow(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            document_version_id=version_id,
            status=JobStatusRow.QUEUED,
            attempt=1,
            run_attempt=1,
            request_id=request_id,
            scheduled_for=func.now(),
            # The caller publishes immediately after commit. If that publish
            # fails, recovery finds this job once the grace period passes.
            enqueued_at=func.now(),
        )
        return await self._insert(row)

    async def restart_failed_version(
        self, ctx: AccessContext, version_id: uuid.UUID, *, request_id: str | None
    ) -> ProcessingJob | None:
        reset = await self._session.execute(
            update(VersionRow)
            .where(
                VersionRow.id == version_id,
                VersionRow.workspace_id == ctx.workspace_id,
                VersionRow.status == ProcessingStatus.FAILED.value,
            )
            .values(
                status=ProcessingStatus.PENDING.value,
                failure_code=None,
                failure_reason=None,
                processed_at=None,
            )
            .returning(VersionRow.id)
        )
        if reset.scalar_one_or_none() is None:
            return None

        highest = await self._session.scalar(
            select(func.max(JobRow.attempt)).where(JobRow.document_version_id == version_id)
        )
        row = JobRow(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            document_version_id=version_id,
            status=JobStatusRow.QUEUED,
            attempt=(highest or 0) + 1,
            run_attempt=1,
            request_id=request_id,
            scheduled_for=func.now(),
            enqueued_at=func.now(),
        )
        return await self._insert(row)

    async def list_jobs(self, ctx: AccessContext, version_id: uuid.UUID) -> Sequence[ProcessingJob]:
        rows = (
            (
                await self._session.execute(
                    select(JobRow)
                    .where(
                        JobRow.workspace_id == ctx.workspace_id,
                        JobRow.document_version_id == version_id,
                    )
                    .order_by(JobRow.attempt.desc())
                )
            )
            .scalars()
            .all()
        )
        return [job_to_entity(row) for row in rows]

    # -- worker: claiming ----------------------------------------------------

    async def lock_for_claim(
        self, system: SystemContext, job_id: uuid.UUID
    ) -> ClaimSnapshot | None:
        del system
        job = (
            await self._session.execute(
                select(JobRow).where(JobRow.id == job_id).with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if job is None:
            return None

        # Lock order is job, then version -- the same order the index
        # transaction takes (its fenced job update first, then the version).
        # Opposite orders would let a redelivered message's claim and the
        # running attempt's index transaction deadlock on each other.
        found = (
            await self._session.execute(
                select(VersionRow, DocumentRow.deleted_at)
                .join(
                    DocumentRow,
                    and_(
                        DocumentRow.id == VersionRow.document_id,
                        DocumentRow.workspace_id == VersionRow.workspace_id,
                    ),
                )
                .where(
                    VersionRow.id == job.document_version_id,
                    VersionRow.workspace_id == job.workspace_id,
                )
                .with_for_update(of=VersionRow)
            )
        ).first()
        if found is None:  # pragma: no cover -- the foreign key makes this unreachable
            msg = "A processing job references a version that does not exist."
            raise NotFoundError(msg, job_id=str(job_id))
        version, deleted_at = found
        return ClaimSnapshot(
            job=job_to_entity(job),
            version=version_to_entity(version),
            document_deleted=deleted_at is not None,
        )

    async def job_exists(self, system: SystemContext, job_id: uuid.UUID) -> bool:
        del system
        count = await self._session.scalar(
            select(func.count()).select_from(JobRow).where(JobRow.id == job_id)
        )
        return bool(count)

    async def mark_running(
        self,
        system: SystemContext,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> None:
        del system
        updated = await self._session.execute(
            update(JobRow)
            .where(JobRow.id == job_id, JobRow.status == JobStatus.QUEUED.value)
            .values(
                status=JobStatus.RUNNING.value,
                worker_id=worker_id,
                lease_expires_at=lease_expires_at,
                stage=PipelineStage.CLAIMED.value,
                started_at=func.now(),
            )
            .returning(JobRow.id)
        )
        if updated.scalar_one_or_none() is None:
            # Only reachable if the caller did not hold the claim lock.
            msg = "Job is no longer queued."
            raise ConflictError(msg, job_id=str(job_id))

    async def renew_lease(
        self,
        system: SystemContext,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        lease_expires_at: datetime,
        stage: PipelineStage,
    ) -> bool:
        del system
        updated = await self._session.execute(
            update(JobRow)
            .where(*self._held_by(job_id, worker_id))
            .values(lease_expires_at=lease_expires_at, stage=stage.value)
            .returning(JobRow.id)
        )
        return updated.scalar_one_or_none() is not None

    async def lock_version_for_index(
        self, system: SystemContext, version_id: uuid.UUID
    ) -> tuple[DocumentVersion, bool] | None:
        del system
        found = (
            await self._session.execute(
                select(VersionRow, DocumentRow.deleted_at)
                .join(
                    DocumentRow,
                    and_(
                        DocumentRow.id == VersionRow.document_id,
                        DocumentRow.workspace_id == VersionRow.workspace_id,
                    ),
                )
                .where(VersionRow.id == version_id)
                .with_for_update(of=VersionRow)
            )
        ).first()
        if found is None:
            return None
        version, deleted_at = found
        return version_to_entity(version), deleted_at is not None

    # -- worker: outcomes ----------------------------------------------------

    async def complete_job(
        self, system: SystemContext, job_id: uuid.UUID, *, worker_id: str
    ) -> bool:
        del system
        updated = await self._session.execute(
            update(JobRow)
            .where(*self._held_by(job_id, worker_id))
            .values(
                status=JobStatus.SUCCEEDED.value,
                stage=PipelineStage.DONE.value,
                worker_id=None,
                lease_expires_at=None,
                finished_at=func.now(),
            )
            .returning(JobRow.id)
        )
        return updated.scalar_one_or_none() is not None

    async def fail_job(
        self,
        system: SystemContext,
        job_id: uuid.UUID,
        *,
        failure: ProcessingFailure,
        worker_id: str | None,
        now: datetime,
    ) -> bool:
        del system
        guard: tuple[ColumnElement[bool], ...]
        if worker_id is not None:
            guard = self._held_by(job_id, worker_id)
        else:
            guard = (
                JobRow.id == job_id,
                or_(
                    and_(
                        JobRow.status == JobStatus.RUNNING.value,
                        JobRow.lease_expires_at < now,
                    ),
                    JobRow.status == JobStatus.QUEUED.value,
                ),
            )
        updated = await self._session.execute(
            update(JobRow)
            .where(*guard)
            .values(
                status=JobStatus.FAILED.value,
                error_code=failure.code,
                failure_kind=failure.kind.value,
                error_message=failure.bounded_detail(),
                worker_id=None,
                lease_expires_at=None,
                # A queued job closed out without running still satisfies
                # "finished implies started": it started and finished at once.
                started_at=func.coalesce(JobRow.started_at, func.now()),
                finished_at=func.now(),
            )
            .returning(JobRow.id)
        )
        return updated.scalar_one_or_none() is not None

    async def schedule_retry(
        self,
        system: SystemContext,
        previous: ProcessingJob,
        *,
        scheduled_for: datetime,
    ) -> ProcessingJob:
        del system
        row = JobRow(
            id=new_uuid7(),
            workspace_id=previous.workspace_id,
            document_version_id=previous.document_version_id,
            status=JobStatusRow.QUEUED,
            attempt=previous.attempt + 1,
            run_attempt=previous.run_attempt + 1,
            # The same request id across every attempt: one query retrieves
            # the upload and all of its retries (ADR-0015).
            request_id=previous.request_id,
            scheduled_for=scheduled_for,
            enqueued_at=func.now(),
        )
        return await self._insert(row)

    async def transition_version(
        self,
        system: SystemContext,
        version_id: uuid.UUID,
        *,
        expected: frozenset[ProcessingStatus],
        outcome: ProcessingOutcome,
    ) -> bool:
        del system
        values: dict[str, object] = {
            "status": outcome.status.value,
            "failure_code": outcome.failure_code,
            "failure_reason": outcome.failure_reason,
            "processed_at": func.now() if outcome.status.is_terminal else None,
        }
        if outcome.chunk_count is not None:
            values["chunk_count"] = outcome.chunk_count
        if outcome.page_count is not None:
            values["page_count"] = outcome.page_count
        updated = await self._session.execute(
            update(VersionRow)
            .where(
                VersionRow.id == version_id,
                VersionRow.status.in_([status.value for status in expected]),
            )
            .values(**values)
            .returning(VersionRow.id)
        )
        return updated.scalar_one_or_none() is not None

    async def replace_chunks(
        self,
        system: SystemContext,
        version: DocumentVersion,
        chunks: Sequence[EmbeddedChunk],
        *,
        space: EmbeddingSpace,
        chunker_version: str,
    ) -> IndexResult:
        del system
        await self._session.execute(
            delete(ChunkRow).where(ChunkRow.document_version_id == version.id)
        )
        rows = [
            {
                "id": new_uuid7(),
                "workspace_id": version.workspace_id,
                "document_id": version.document_id,
                "document_version_id": version.id,
                "ordinal": chunk.draft.ordinal,
                "content": chunk.draft.text,
                "content_sha256": chunk.draft.content_sha256,
                "token_count": chunk.draft.token_count,
                "char_start": chunk.draft.char_start,
                "char_end": chunk.draft.char_end,
                "page_from": chunk.draft.page_start,
                "page_to": chunk.draft.page_end,
                "heading_path": chunk.draft.heading_path_text,
                "embedding": chunk.embedding,
                "embedding_model": space.model,
                "embedding_dimensions": space.dimensions,
                "embedding_input_sha256": chunk.draft.embedding_input_sha256,
                "embedded_at": chunk.embedded_at,
                "chunker_version": chunker_version,
            }
            for chunk in chunks
        ]
        try:
            for start in range(0, len(rows), _INSERT_BATCH_SIZE):
                await self._session.execute(
                    insert(ChunkRow), rows[start : start + _INSERT_BATCH_SIZE]
                )
        except IntegrityError as exc:
            raise translate_integrity_error(exc) from exc
        return IndexResult(chunk_count=len(rows))

    # -- recovery --------------------------------------------------------------

    async def find_abandoned(
        self, system: SystemContext, *, now: datetime, limit: int
    ) -> Sequence[uuid.UUID]:
        del system
        result = await self._session.execute(
            select(JobRow.id)
            .where(JobRow.status == JobStatus.RUNNING.value, JobRow.lease_expires_at < now)
            .order_by(JobRow.lease_expires_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def find_undelivered(
        self, system: SystemContext, *, stale_before: datetime, limit: int
    ) -> Sequence[ProcessingJob]:
        del system
        rows = (
            (
                await self._session.execute(
                    select(JobRow)
                    .where(
                        JobRow.status == JobStatus.QUEUED.value,
                        JobRow.scheduled_for < stale_before,
                        or_(JobRow.enqueued_at.is_(None), JobRow.enqueued_at < stale_before),
                    )
                    .order_by(JobRow.scheduled_for)
                    .limit(limit)
                    # Two recovery sweeps running at once each take a disjoint
                    # set instead of both re-publishing the same jobs.
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        return [job_to_entity(row) for row in rows]

    async def mark_enqueued(
        self, system: SystemContext, job_ids: Sequence[uuid.UUID], *, at: datetime
    ) -> None:
        del system
        if not job_ids:
            return
        await self._session.execute(
            update(JobRow).where(JobRow.id.in_(job_ids)).values(enqueued_at=at)
        )

    async def queue_snapshot(self, system: SystemContext, *, now: datetime) -> QueueSnapshot:
        del system
        queued = JobRow.status == JobStatus.QUEUED.value
        running = JobRow.status == JobStatus.RUNNING.value
        due = JobRow.scheduled_for <= now
        expired = and_(running, JobRow.lease_expires_at < now)
        row = (
            await self._session.execute(
                select(
                    func.count().filter(and_(queued, due)),
                    func.count().filter(and_(queued, ~due)),
                    func.count().filter(and_(running, ~expired)),
                    func.count().filter(expired),
                    func.min(JobRow.scheduled_for).filter(and_(queued, due)),
                )
                # Only live jobs: this reads the two partial indexes, not the
                # table's history of finished attempts.
                .where(JobRow.status.in_((JobStatus.QUEUED.value, JobStatus.RUNNING.value)))
            )
        ).one()
        ready, scheduled, live, abandoned, oldest = row
        return QueueSnapshot(
            ready=ready,
            scheduled=scheduled,
            running=live,
            lease_expired=abandoned,
            oldest_ready_age_seconds=(
                max((now - oldest).total_seconds(), 0.0) if oldest is not None else None
            ),
        )

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _held_by(job_id: uuid.UUID, worker_id: str) -> tuple[ColumnElement[bool], ...]:
        """The fence: this job, still running, still held by this execution."""
        return (
            JobRow.id == job_id,
            JobRow.status == JobStatus.RUNNING.value,
            JobRow.worker_id == worker_id,
        )

    async def _insert(self, row: JobRow) -> ProcessingJob:
        self._session.add(row)
        await flush_translating_conflicts(self._session)
        await self._session.refresh(row)
        return job_to_entity(row)
