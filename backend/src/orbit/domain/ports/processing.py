"""Ports for the document processing pipeline.

Each stage that touches a library, a provider, or the network is a Protocol
here and an adapter in `infrastructure`, so the orchestrator in
`application/processing` can be tested end to end with no PDF library, no
broker, and no embedding provider -- and so any single stage can be replaced
(a semantic chunker, an OCR parser, a different provider) without the others
noticing.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import IO, Protocol

from orbit.domain.access import AccessContext, SystemContext
from orbit.domain.embeddings import EmbeddingSpace
from orbit.domain.models.entities import DocumentVersion, ProcessingOutcome, ProcessingStatus
from orbit.domain.processing.content import (
    ChunkDraft,
    EmbeddedChunk,
    ExtractedDocument,
    ParsedDocument,
    ParseLimits,
)
from orbit.domain.processing.failures import ProcessingFailure
from orbit.domain.processing.jobs import (
    ClaimSnapshot,
    PipelineStage,
    ProcessingJob,
    QueueSnapshot,
)

# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------


class DocumentParser(Protocol):
    """Bytes of one format in, an `ExtractedDocument` out (ADR-0012).

    Implementations treat their input as hostile. Any failure caused by the
    file's content is raised as a `DocumentProcessingError` subclass with a
    user-safe message -- never a library's raw exception, which the classifier
    would otherwise have to treat as a defect in ORBIT.
    """

    @property
    def content_types(self) -> frozenset[str]: ...

    def parse(self, source: IO[bytes], *, limits: ParseLimits) -> ExtractedDocument: ...


class ParserRegistry(Protocol):
    def resolve(self, content_type: str) -> DocumentParser:
        """Raises `DocumentFormatUnsupportedError` for an unknown type -- never a
        fallback to "treat it as text" (ADR-0012)."""
        ...


class TextNormalizer(Protocol):
    """The one shared normalization stage. Assigns offsets, so it runs before
    anything that needs them."""

    def normalize(self, document: ExtractedDocument, *, limits: ParseLimits) -> ParsedDocument: ...


class Chunker(Protocol):
    """Pure and deterministic: same document, same configuration, same chunks."""

    @property
    def version(self) -> str: ...

    def chunk(self, document: ParsedDocument) -> Sequence[ChunkDraft]: ...


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


class FailureClassifier(Protocol):
    """Turns *any* exception raised during processing into a classified failure.

    Deliberately a port: the exceptions worth distinguishing -- a database
    driver's connection error, an HTTP client's timeout, the task runtime's
    soft time limit -- belong to libraries the application layer may not
    import.
    """

    def classify(self, exc: BaseException) -> ProcessingFailure: ...


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JobDispatch:
    """A queue message. Identifiers only, never content (ADR-0002)."""

    job_id: uuid.UUID
    request_id: str | None = None
    delay: timedelta = timedelta(0)


class ProcessingJobQueue(Protocol):
    """Rings the doorbell. The job row, not the message, is the source of truth.

    A message that is lost costs latency, not correctness: recovery re-sends
    for any job still queued past a grace period. A message delivered twice
    costs nothing: claiming is conditional.
    """

    async def enqueue(self, dispatch: JobDispatch) -> None:
        """Raises `QueueUnavailableError` if the broker refused it."""
        ...


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IndexResult:
    chunk_count: int


class ProcessingRepository(Protocol):
    """Jobs, chunks, and the pipeline's version status transitions.

    Two kinds of caller, two kinds of authority. The upload and reprocess paths
    act for a user in a workspace and pass an `AccessContext`. The worker acts
    on a job id it received from a queue, belongs to no user, and passes a
    `SystemContext` -- visibly privileged in every signature. Every write the
    worker makes on a claimed job is **fenced** on `worker_id`: it applies only
    while that execution still holds the lease, and reports whether it did.
    """

    # -- user-initiated ------------------------------------------------------

    async def create_initial_job(
        self, ctx: AccessContext, version_id: uuid.UUID, *, request_id: str | None
    ) -> ProcessingJob:
        """The first job for a freshly uploaded version, in the upload's own
        transaction -- so a version never exists without the job that will
        process it."""
        ...

    async def restart_failed_version(
        self, ctx: AccessContext, version_id: uuid.UUID, *, request_id: str | None
    ) -> ProcessingJob | None:
        """FAILED -> PENDING plus a fresh job with a new retry budget.

        Returns `None` if the version is not currently FAILED, which is the
        caller's conflict to report.
        """
        ...

    async def list_jobs(self, ctx: AccessContext, version_id: uuid.UUID) -> Sequence[ProcessingJob]:
        """Newest attempt first."""
        ...

    # -- worker: claiming ----------------------------------------------------

    async def lock_for_claim(
        self, system: SystemContext, job_id: uuid.UUID
    ) -> ClaimSnapshot | None:
        """Lock the job and its version (`FOR UPDATE SKIP LOCKED`).

        `None` means the job does not exist *or* another transaction holds it
        right now; `job_exists` tells the two apart. Skipping rather than
        waiting is deliberate: a second delivery waiting on the first worker's
        claim would only then discover there is nothing to do.
        """
        ...

    async def job_exists(self, system: SystemContext, job_id: uuid.UUID) -> bool: ...

    async def mark_running(
        self,
        system: SystemContext,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> None:
        """QUEUED -> RUNNING. Only valid under the claim's row lock."""
        ...

    async def renew_lease(
        self,
        system: SystemContext,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        lease_expires_at: datetime,
        stage: PipelineStage,
    ) -> bool:
        """Heartbeat. `False` means the lease is gone and the caller must stop."""
        ...

    async def lock_version_for_index(
        self, system: SystemContext, version_id: uuid.UUID
    ) -> tuple[DocumentVersion, bool] | None:
        """Lock the version row for the index transaction; returns it and
        whether its document is deleted. Serializes against a concurrent
        upload of a newer version, which demotes this one under the same
        lock."""
        ...

    # -- worker: outcomes ----------------------------------------------------

    async def complete_job(
        self, system: SystemContext, job_id: uuid.UUID, *, worker_id: str
    ) -> bool:
        """RUNNING -> SUCCEEDED, fenced."""
        ...

    async def fail_job(
        self,
        system: SystemContext,
        job_id: uuid.UUID,
        *,
        failure: ProcessingFailure,
        worker_id: str | None,
        now: datetime,
    ) -> bool:
        """-> FAILED, fenced.

        With `worker_id`, applies only while that execution holds the lease.
        With `worker_id=None` -- recovery of an abandoned job -- applies only to
        a RUNNING job whose lease expired before `now`, or to a QUEUED job
        (a superseded or inconsistent one being closed out).
        """
        ...

    async def schedule_retry(
        self,
        system: SystemContext,
        previous: ProcessingJob,
        *,
        scheduled_for: datetime,
    ) -> ProcessingJob:
        """Insert the next attempt as QUEUED."""
        ...

    async def transition_version(
        self,
        system: SystemContext,
        version_id: uuid.UUID,
        *,
        expected: frozenset[ProcessingStatus],
        outcome: ProcessingOutcome,
    ) -> bool:
        """Conditional status change; `False` if the version was not in an
        expected state. A stale actor cannot regress READY to PROCESSING."""
        ...

    async def replace_chunks(
        self,
        system: SystemContext,
        version: DocumentVersion,
        chunks: Sequence[EmbeddedChunk],
        *,
        space: EmbeddingSpace,
        chunker_version: str,
    ) -> IndexResult:
        """Delete the version's chunks and insert these, in the caller's
        transaction. Deleting first is what makes a re-run after a partial
        attempt produce one set of chunks, never two.

        Every chunk is written with its vector and the space it lives in; a
        chunk row without a vector is unrepresentable (migration 0004)."""
        ...

    # -- recovery --------------------------------------------------------------

    async def find_abandoned(
        self, system: SystemContext, *, now: datetime, limit: int
    ) -> Sequence[uuid.UUID]:
        """RUNNING jobs whose lease expired before `now`."""
        ...

    async def find_undelivered(
        self, system: SystemContext, *, stale_before: datetime, limit: int
    ) -> Sequence[ProcessingJob]:
        """QUEUED jobs that were due, and last enqueued, before `stale_before`."""
        ...

    async def mark_enqueued(
        self, system: SystemContext, job_ids: Sequence[uuid.UUID], *, at: datetime
    ) -> None: ...

    async def queue_snapshot(self, system: SystemContext, *, now: datetime) -> QueueSnapshot:
        """Counts of live jobs by state, for the queue gauges."""
        ...
