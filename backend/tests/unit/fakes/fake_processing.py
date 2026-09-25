"""In-memory `ProcessingRepository`, plus a recording job queue.

Mirrors the SQL adapter's *conditions* -- the fence on `worker_id`, the
expected-state guard on version transitions, the one-active-job-per-version
rule -- because those conditions are the behaviour under test. It does not
mirror row locking: tests that need "another transaction holds this job" add
the id to `state.locked_job_ids`. Real locking is covered by the integration
suite against PostgreSQL.
"""

from __future__ import annotations

import dataclasses
import math
import re
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from orbit.core.ids import new_uuid7
from orbit.domain.access import AccessContext, SystemContext
from orbit.domain.embeddings import EmbeddingSpace
from orbit.domain.errors import ConflictError, QueueUnavailableError
from orbit.domain.models.entities import DocumentVersion, ProcessingOutcome, ProcessingStatus
from orbit.domain.ports.embeddings import (
    EmbeddingUpdate,
    IndexCoverage,
    SpaceUsage,
    StaleChunk,
    StoredEmbedding,
)
from orbit.domain.ports.processing import IndexResult, JobDispatch
from orbit.domain.processing.content import EmbeddedChunk
from orbit.domain.processing.failures import ProcessingFailure
from orbit.domain.processing.jobs import (
    ClaimSnapshot,
    JobStatus,
    PipelineStage,
    ProcessingJob,
    QueueSnapshot,
)
from orbit.domain.retrieval import Candidate, ChunkRecord, LexicalMatch, SearchFilters

if TYPE_CHECKING:
    from tests.unit.fakes.in_memory_unit_of_work import _State


@dataclass(frozen=True, slots=True)
class StoredChunk:
    version_id: uuid.UUID
    document_id: uuid.UUID
    ordinal: int
    text: str
    embedding_model: str
    chunker_version: str
    page_start: int | None
    page_end: int | None
    workspace_id: uuid.UUID
    char_start: int
    char_end: int
    heading_path: str | None
    token_count: int
    embedding: Sequence[float]
    embedding_dimensions: int
    embedding_input_sha256: str
    embedded_at: datetime
    chunk_id: uuid.UUID = field(default_factory=new_uuid7)

    @property
    def space(self) -> EmbeddingSpace:
        return EmbeddingSpace(model=self.embedding_model, dimensions=self.embedding_dimensions)


class FakeProcessingRepository:
    def __init__(self, state: _State) -> None:
        self._state = state

    # -- helpers ---------------------------------------------------------------

    def _now(self) -> datetime:
        return (
            self._state.controls.clock_now()
            if self._state.controls.clock_now
            else datetime.now(UTC)
        )

    def _insert(self, job: ProcessingJob) -> ProcessingJob:
        active = [
            existing
            for existing in self._state.jobs.values()
            if existing.document_version_id == job.document_version_id
            and existing.status in (JobStatus.QUEUED, JobStatus.RUNNING)
        ]
        if active:
            msg = "This document is already being processed."
            raise ConflictError(msg, constraint="uq_jobs_active_per_version")
        self._state.jobs[job.id] = job
        return job

    def _new_job(self, version: DocumentVersion, **values: object) -> ProcessingJob:
        now = self._now()
        base = ProcessingJob(
            id=new_uuid7(),
            workspace_id=version.workspace_id,
            document_version_id=version.id,
            status=JobStatus.QUEUED,
            attempt=1,
            run_attempt=1,
            scheduled_for=now,
            created_at=now,
            enqueued_at=now,
        )
        return dataclasses.replace(base, **values)  # type: ignore[arg-type]

    def _held(self, job_id: uuid.UUID, worker_id: str) -> ProcessingJob | None:
        job = self._state.jobs.get(job_id)
        if job is None or job.status is not JobStatus.RUNNING or job.worker_id != worker_id:
            return None
        return job

    # -- user-initiated ------------------------------------------------------

    async def create_initial_job(
        self, ctx: AccessContext, version_id: uuid.UUID, *, request_id: str | None
    ) -> ProcessingJob:
        version = self._state.versions[version_id]
        assert version.workspace_id == ctx.workspace_id
        return self._insert(self._new_job(version, request_id=request_id))

    async def restart_failed_version(
        self, ctx: AccessContext, version_id: uuid.UUID, *, request_id: str | None
    ) -> ProcessingJob | None:
        version = self._state.versions.get(version_id)
        if (
            version is None
            or version.workspace_id != ctx.workspace_id
            or version.status is not ProcessingStatus.FAILED
        ):
            return None
        self._state.versions[version_id] = dataclasses.replace(
            version,
            status=ProcessingStatus.PENDING,
            failure_code=None,
            failure_reason=None,
            processed_at=None,
        )
        highest = max(
            (
                job.attempt
                for job in self._state.jobs.values()
                if job.document_version_id == version_id
            ),
            default=0,
        )
        return self._insert(self._new_job(version, attempt=highest + 1, request_id=request_id))

    async def list_jobs(self, ctx: AccessContext, version_id: uuid.UUID) -> Sequence[ProcessingJob]:
        return sorted(
            (
                job
                for job in self._state.jobs.values()
                if job.document_version_id == version_id and job.workspace_id == ctx.workspace_id
            ),
            key=lambda job: job.attempt,
            reverse=True,
        )

    # -- worker: claiming ----------------------------------------------------

    async def lock_for_claim(
        self, system: SystemContext, job_id: uuid.UUID
    ) -> ClaimSnapshot | None:
        del system
        job = self._state.jobs.get(job_id)
        if job is None or job_id in self._state.controls.locked_job_ids:
            return None
        version = self._state.versions[job.document_version_id]
        document = self._state.documents[version.document_id]
        return ClaimSnapshot(job=job, version=version, document_deleted=document.is_deleted)

    async def job_exists(self, system: SystemContext, job_id: uuid.UUID) -> bool:
        del system
        return job_id in self._state.jobs

    async def mark_running(
        self,
        system: SystemContext,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> None:
        del system
        job = self._state.jobs[job_id]
        if job.status is not JobStatus.QUEUED:
            msg = "Job is no longer queued."
            raise ConflictError(msg)
        self._state.jobs[job_id] = dataclasses.replace(
            job,
            status=JobStatus.RUNNING,
            worker_id=worker_id,
            lease_expires_at=lease_expires_at,
            stage=PipelineStage.CLAIMED,
            started_at=self._now(),
        )

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
        job = self._held(job_id, worker_id)
        if job is None:
            return False
        self._state.jobs[job_id] = dataclasses.replace(
            job, lease_expires_at=lease_expires_at, stage=stage
        )
        return True

    async def lock_version_for_index(
        self, system: SystemContext, version_id: uuid.UUID
    ) -> tuple[DocumentVersion, bool] | None:
        del system
        version = self._state.versions.get(version_id)
        if version is None:
            return None
        return version, self._state.documents[version.document_id].is_deleted

    # -- worker: outcomes ----------------------------------------------------

    async def complete_job(
        self, system: SystemContext, job_id: uuid.UUID, *, worker_id: str
    ) -> bool:
        del system
        job = self._held(job_id, worker_id)
        if job is None:
            return False
        self._state.jobs[job_id] = dataclasses.replace(
            job,
            status=JobStatus.SUCCEEDED,
            stage=PipelineStage.DONE,
            worker_id=None,
            lease_expires_at=None,
            finished_at=self._now(),
        )
        return True

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
        job = self._state.jobs.get(job_id)
        if job is None:
            return False
        if worker_id is not None:
            if self._held(job_id, worker_id) is None:
                return False
        elif not (
            (
                job.status is JobStatus.RUNNING
                and job.lease_expires_at is not None
                and job.lease_expires_at < now
            )
            or job.status is JobStatus.QUEUED
        ):
            return False
        self._state.jobs[job_id] = dataclasses.replace(
            job,
            status=JobStatus.FAILED,
            error_code=failure.code,
            failure_kind=failure.kind,
            error_message=failure.bounded_detail(),
            worker_id=None,
            lease_expires_at=None,
            started_at=job.started_at or self._now(),
            finished_at=self._now(),
        )
        return True

    async def schedule_retry(
        self, system: SystemContext, previous: ProcessingJob, *, scheduled_for: datetime
    ) -> ProcessingJob:
        del system
        version = self._state.versions[previous.document_version_id]
        return self._insert(
            self._new_job(
                version,
                attempt=previous.attempt + 1,
                run_attempt=previous.run_attempt + 1,
                request_id=previous.request_id,
                scheduled_for=scheduled_for,
            )
        )

    async def transition_version(
        self,
        system: SystemContext,
        version_id: uuid.UUID,
        *,
        expected: frozenset[ProcessingStatus],
        outcome: ProcessingOutcome,
    ) -> bool:
        del system
        version = self._state.versions.get(version_id)
        if version is None or version.status not in expected:
            return False
        self._state.versions[version_id] = dataclasses.replace(
            version,
            status=outcome.status,
            failure_code=outcome.failure_code,
            failure_reason=outcome.failure_reason,
            chunk_count=outcome.chunk_count
            if outcome.chunk_count is not None
            else version.chunk_count,
            page_count=outcome.page_count if outcome.page_count is not None else version.page_count,
            processed_at=self._now() if outcome.status.is_terminal else None,
        )
        return True

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
        rows = [
            StoredChunk(
                version_id=version.id,
                document_id=version.document_id,
                ordinal=chunk.draft.ordinal,
                text=chunk.draft.text,
                embedding_model=space.model,
                chunker_version=chunker_version,
                page_start=chunk.draft.page_start,
                page_end=chunk.draft.page_end,
                workspace_id=version.workspace_id,
                char_start=chunk.draft.char_start,
                char_end=chunk.draft.char_end,
                heading_path=chunk.draft.heading_path_text,
                token_count=chunk.draft.token_count,
                embedding=chunk.embedding,
                # What the vector actually is, not what the space claims --
                # mirroring the SQL CHECK that ties the two together.
                embedding_dimensions=len(chunk.embedding),
                embedding_input_sha256=chunk.draft.embedding_input_sha256,
                embedded_at=chunk.embedded_at,
            )
            for chunk in chunks
        ]
        if any(row.embedding_dimensions != space.dimensions for row in rows):
            msg = "ck_chunks_embedding_dimensions_match_vector violated"
            raise RuntimeError(msg)
        if self._state.controls.fail_next_index is not None:
            error, self._state.controls.fail_next_index = self._state.controls.fail_next_index, None
            # Write some chunks first, so the test proves the rollback removes
            # them rather than proving nothing was ever written.
            self._state.chunks[version.id] = rows[:1]
            raise error
        self._state.chunks[version.id] = rows
        return IndexResult(chunk_count=len(rows))

    # -- recovery --------------------------------------------------------------

    async def find_abandoned(
        self, system: SystemContext, *, now: datetime, limit: int
    ) -> Sequence[uuid.UUID]:
        del system
        return [
            job.id
            for job in self._state.jobs.values()
            if job.status is JobStatus.RUNNING
            and job.lease_expires_at is not None
            and job.lease_expires_at < now
        ][:limit]

    async def find_undelivered(
        self, system: SystemContext, *, stale_before: datetime, limit: int
    ) -> Sequence[ProcessingJob]:
        del system
        return [
            job
            for job in self._state.jobs.values()
            if job.status is JobStatus.QUEUED
            and job.scheduled_for < stale_before
            and (job.enqueued_at is None or job.enqueued_at < stale_before)
        ][:limit]

    async def mark_enqueued(
        self, system: SystemContext, job_ids: Sequence[uuid.UUID], *, at: datetime
    ) -> None:
        del system
        for job_id in job_ids:
            self._state.jobs[job_id] = dataclasses.replace(self._state.jobs[job_id], enqueued_at=at)

    async def queue_snapshot(self, system: SystemContext, *, now: datetime) -> QueueSnapshot:
        del system
        jobs = list(self._state.jobs.values())
        ready = [j for j in jobs if j.status is JobStatus.QUEUED and j.scheduled_for <= now]
        running = [j for j in jobs if j.status is JobStatus.RUNNING]
        expired = [j for j in running if j.lease_expires_at and j.lease_expires_at < now]
        return QueueSnapshot(
            ready=len(ready),
            scheduled=sum(
                1 for j in jobs if j.status is JobStatus.QUEUED and j.scheduled_for > now
            ),
            running=len(running) - len(expired),
            lease_expired=len(expired),
            oldest_ready_age_seconds=(
                max((now - min(j.scheduled_for for j in ready)).total_seconds(), 0.0)
                if ready
                else None
            ),
        )


@dataclass
class RecordingJobQueue:
    """Records every dispatch. `fail` makes the next publishes raise, as a broker
    outage would."""

    dispatched: list[JobDispatch] = field(default_factory=list)
    fail: bool = False

    async def enqueue(self, dispatch: JobDispatch) -> None:
        if self.fail:
            msg = "Simulated broker outage."
            raise QueueUnavailableError(msg)
        self.dispatched.append(dispatch)

    def take(self) -> list[JobDispatch]:
        taken, self.dispatched = self.dispatched, []
        return taken


def _live_chunks(state: _State) -> list[StoredChunk]:
    """Chunks of current versions of undeleted documents, in id order."""
    live = []
    for version_id, rows in state.chunks.items():
        version = state.versions.get(version_id)
        if version is None or not version.is_current:
            continue
        if state.documents[version.document_id].is_deleted:
            continue
        live.extend(rows)
    return sorted(live, key=lambda row: row.chunk_id)


class FakeEmbeddingIndexRepository:
    def __init__(self, state: _State) -> None:
        self._state = state

    async def column_dimensions(self, system: SystemContext) -> int | None:
        del system
        return self._state.controls.column_dimensions

    async def find_reusable(
        self,
        system: SystemContext,
        *,
        workspace_id: uuid.UUID,
        input_hashes: Collection[str],
        space: EmbeddingSpace,
    ) -> Mapping[str, StoredEmbedding]:
        del system
        wanted = set(input_hashes)
        self._state.controls.reuse_lookups.append(workspace_id)
        found: dict[str, StoredEmbedding] = {}
        for rows in self._state.chunks.values():
            for row in rows:
                if (
                    row.workspace_id == workspace_id
                    and row.space == space
                    and row.embedding_input_sha256 in wanted
                ):
                    found[row.embedding_input_sha256] = StoredEmbedding(
                        vector=row.embedding, embedded_at=row.embedded_at
                    )
        return found

    async def count_indexed(
        self, system: SystemContext, version_id: uuid.UUID, *, space: EmbeddingSpace
    ) -> int:
        del system
        return sum(1 for row in self._state.chunks.get(version_id, []) if row.space == space)

    async def coverage(self, system: SystemContext, *, space: EmbeddingSpace) -> IndexCoverage:
        grouped: dict[tuple[str, int], list[StoredChunk]] = {}
        chunkers: dict[str, int] = {}
        for row in _live_chunks(self._state):
            grouped.setdefault((row.embedding_model, row.embedding_dimensions), []).append(row)
            chunkers[row.chunker_version] = chunkers.get(row.chunker_version, 0) + 1
        return IndexCoverage(
            active=space,
            column_dimensions=await self.column_dimensions(system),
            spaces=tuple(
                SpaceUsage(
                    model=model,
                    dimensions=dimensions,
                    chunks=len(rows),
                    versions=len({row.version_id for row in rows}),
                    oldest_embedded_at=min(row.embedded_at for row in rows),
                    newest_embedded_at=max(row.embedded_at for row in rows),
                )
                for (model, dimensions), rows in sorted(grouped.items())
            ),
            chunker_versions=chunkers,
        )

    async def find_stale(
        self,
        system: SystemContext,
        *,
        space: EmbeddingSpace,
        after: uuid.UUID | None,
        limit: int,
    ) -> Sequence[StaleChunk]:
        del system
        return [
            StaleChunk(
                chunk_id=row.chunk_id,
                workspace_id=row.workspace_id,
                content=row.text,
                heading_path=row.heading_path,
                token_count=row.token_count,
                embedding_input_sha256=row.embedding_input_sha256,
            )
            for row in _live_chunks(self._state)
            if row.space != space and (after is None or row.chunk_id > after)
        ][:limit]

    async def update_embeddings(
        self,
        system: SystemContext,
        updates: Sequence[EmbeddingUpdate],
        *,
        space: EmbeddingSpace,
    ) -> int:
        del system
        by_id = {update.chunk_id: update for update in updates}
        changed = 0
        for version_id, rows in self._state.chunks.items():
            replaced = []
            for row in rows:
                update = by_id.get(row.chunk_id)
                if (
                    update is not None
                    and update.embedding_input_sha256 == row.embedding_input_sha256
                    and row.space != space
                ):
                    replaced.append(
                        dataclasses.replace(
                            row,
                            embedding=update.vector,
                            embedding_model=space.model,
                            embedding_dimensions=len(update.vector),
                            embedded_at=update.embedded_at,
                        )
                    )
                    changed += 1
                else:
                    replaced.append(row)
            self._state.chunks[version_id] = replaced
        return changed


class FakeSearchRepository:
    """Both retrievers by brute force, with the SQL adapter's visibility rules.

    Lexical matching is a crude stand-in for PostgreSQL full-text search --
    case-folded word overlap, no stemming, no stop words -- sufficient for
    testing fusion and authorization in the use case. Real full-text
    behaviour is tested against PostgreSQL in the integration suite.
    """

    def __init__(self, state: _State) -> None:
        self._state = state

    def _visible(self, ctx: AccessContext, filters: SearchFilters) -> list[StoredChunk]:
        return [row for row in _live_chunks(self._state) if self._passes(ctx, filters, row)]

    def _passes(self, ctx: AccessContext, filters: SearchFilters, row: StoredChunk) -> bool:
        """The fake's copy of `_visible` in the SQL repository.

        Written as the same conjunction, so a filter that narrows here
        narrows there: a unit test passing against a filter the real query
        ignores would be worse than no test at all.
        """
        document = self._state.documents[row.document_id]
        version = self._state.versions[row.version_id]
        return all(
            (
                row.workspace_id == ctx.workspace_id,
                version.status is ProcessingStatus.READY,
                document.archived_at is None,
                filters.document_ids is None or row.document_id in filters.document_ids,
                filters.folder_id is None or document.folder_id == filters.folder_id,
                not filters.unfiled or document.folder_id is None,
                filters.content_types is None or version.content_type in filters.content_types,
                filters.tag_ids is None
                or all(
                    (row.document_id, tag_id) in self._state.document_tags
                    for tag_id in filters.tag_ids
                ),
            )
        )

    async def lexical_candidates(
        self,
        ctx: AccessContext,
        text: str,
        *,
        match: LexicalMatch,
        filters: SearchFilters,
        limit: int,
    ) -> Sequence[Candidate]:
        terms = set(_WORD.findall(text.casefold()))
        scored = []
        for row in self._visible(ctx, filters):
            words = set(_WORD.findall(row.text.casefold()))
            hits = len(terms & words)
            if hits and (match is LexicalMatch.ANY or terms <= words):
                scored.append(Candidate(chunk_id=row.chunk_id, score=hits / len(terms)))
        scored.sort(key=lambda c: (-c.score, c.chunk_id))
        return scored[:limit]

    async def semantic_candidates(  # noqa: PLR0913
        self,
        ctx: AccessContext,
        vector: Sequence[float],
        *,
        space: EmbeddingSpace,
        filters: SearchFilters,
        limit: int,
        ef_search: int,
    ) -> Sequence[Candidate]:
        del ef_search
        scored = [
            Candidate(chunk_id=row.chunk_id, score=1.0 - cosine_distance(vector, row.embedding))
            for row in self._visible(ctx, filters)
            if row.space == space
        ]
        scored.sort(key=lambda c: (-c.score, c.chunk_id))
        return scored[:limit]

    async def load_results(
        self,
        ctx: AccessContext,
        chunk_ids: Collection[uuid.UUID],
        *,
        filters: SearchFilters,
    ) -> Mapping[uuid.UUID, ChunkRecord]:
        wanted = set(chunk_ids)
        records = {}
        for row in self._visible(ctx, filters):
            if row.chunk_id not in wanted:
                continue
            version = self._state.versions[row.version_id]
            records[row.chunk_id] = ChunkRecord(
                chunk_id=row.chunk_id,
                workspace_id=row.workspace_id,
                document_id=row.document_id,
                document_title=self._state.documents[row.document_id].title,
                content_type=version.content_type,
                document_updated_at=self._state.documents[row.document_id].updated_at,
                version_id=row.version_id,
                version_number=version.version_number,
                ordinal=row.ordinal,
                content=row.text,
                heading_path=row.heading_path,
                page_from=row.page_start,
                page_to=row.page_end,
                char_start=row.char_start,
                char_end=row.char_end,
            )
        return records


_WORD = re.compile(r"\w+")


def cosine_distance(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(left, right, strict=True))
    return 1.0 - dot / (math.hypot(*left) * math.hypot(*right))
