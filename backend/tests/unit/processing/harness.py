"""A complete processing pipeline on in-memory fakes.

Real use cases, real parsers, real normalizer, real chunker, real classifier --
only the database, object storage, the broker, and the clock are fakes. What a
test drives is exactly what the worker drives.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from orbit.application.auth.rate_limits import AuthRateLimitGuard, RateLimitPolicy
from orbit.application.documents.add_document_version import AddDocumentVersion
from orbit.application.documents.upload_document import UploadDocument
from orbit.application.embeddings.embed_chunks import ChunkEmbedder
from orbit.application.processing.get_processing_status import GetProcessingStatus
from orbit.application.processing.lifecycle import PROCESSING_SYSTEM, JobLifecycle, JobRunResult
from orbit.application.processing.policy import ProcessingPolicy
from orbit.application.processing.process_document import PipelineStages, ProcessDocumentJob
from orbit.application.processing.recover_stalled_jobs import RecoverStalledJobs
from orbit.application.processing.reprocess_document import ReprocessDocument
from orbit.application.workspaces.create_workspace import CreateWorkspace
from orbit.core.clock import FixedClock
from orbit.domain.access import AccessContext, Role
from orbit.domain.embeddings import EmbeddingSpace
from orbit.domain.models.entities import Document, DocumentVersion
from orbit.domain.processing.content import ParseLimits
from orbit.domain.processing.jobs import ProcessingJob
from orbit.domain.processing.retry import RetryPolicy
from orbit.infrastructure.ai.fake_embeddings import FakeEmbeddingProvider
from orbit.infrastructure.chunking.structure_aware import StructureAwareChunker
from orbit.infrastructure.parsing.normalization import DocumentNormalizer
from orbit.infrastructure.parsing.registry import default_parser_registry
from orbit.infrastructure.processing.failure_classifier import PipelineFailureClassifier
from tests.conftest import build_settings
from tests.unit.fakes.fake_processing import RecordingJobQueue, StoredChunk
from tests.unit.fakes.fake_storage import FakeObjectStorage
from tests.unit.fakes.in_memory_unit_of_work import FakeUnitOfWorkFactory
from tests.unit.fakes.security_doubles import InMemoryRateLimiter, RecordingAuditSink

LEASE = timedelta(minutes=5)


class WorkerKilledError(BaseException):
    """Stands in for SIGKILL: a BaseException, so nothing in the pipeline
    catches it and nothing further is written -- exactly what a killed process
    leaves behind."""


@dataclass
class ControllableEmbedder:
    """The fake provider, plus scripted failures, scripted outputs, and a
    mid-run hook. Records every input it was asked to embed."""

    inner: FakeEmbeddingProvider = field(
        default_factory=lambda: FakeEmbeddingProvider(dimensions=32)
    )
    failures: list[BaseException] = field(default_factory=list)
    #: When set, returned instead of the real vectors for the next call.
    scripted_outputs: list[Sequence[Sequence[float]]] = field(default_factory=list)
    before_return: Callable[[], Awaitable[None]] | None = None
    batch_size: int = 16
    calls: int = 0
    inputs: list[str] = field(default_factory=list)

    @property
    def space(self) -> EmbeddingSpace:
        return self.inner.space

    @property
    def model_id(self) -> str:
        return self.inner.space.model

    @property
    def max_batch_size(self) -> int:
        return self.batch_size

    @property
    def max_batch_tokens(self) -> int:
        return self.inner.max_batch_tokens

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        self.inputs.extend(texts)
        vectors = (
            self.scripted_outputs.pop(0)
            if self.scripted_outputs
            else await self.inner.embed_documents(texts)
        )
        if self.before_return is not None:
            hook, self.before_return = self.before_return, None
            await hook()
        return vectors

    async def embed_query(self, text: str) -> Sequence[float]:
        self.inputs.append(text)
        return await self.inner.embed_query(text)


@dataclass
class Pipeline:
    uow_factory: FakeUnitOfWorkFactory
    storage: FakeObjectStorage
    queue: RecordingJobQueue
    clock: FixedClock
    policy: ProcessingPolicy
    embedder: ControllableEmbedder
    lifecycle: JobLifecycle
    worker: ProcessDocumentJob
    recovery: RecoverStalledJobs
    upload_document: UploadDocument
    add_version: AddDocumentVersion
    reprocess: ReprocessDocument
    status: GetProcessingStatus
    ctx: AccessContext

    async def upload(
        self, filename: str, data: bytes, ctx: AccessContext | None = None
    ) -> Document:
        async def stream() -> AsyncIterator[bytes]:
            yield data

        result = await self.upload_document.execute(
            ctx or self.ctx, filename=filename, title=None, folder_id=None, content_stream=stream()
        )
        return result.document

    def dispatched_job_ids(self) -> list[uuid.UUID]:
        return [dispatch.job_id for dispatch in self.queue.take()]

    async def run(self, job_id: uuid.UUID, worker_id: str = "worker-a") -> JobRunResult:
        return await self.worker.execute(job_id, worker_id=worker_id)

    async def drain(self, worker_id: str = "worker-a") -> list[JobRunResult]:
        """Deliver every published message, as a broker would, after its delay."""
        results = []
        while self.queue.dispatched:
            dispatch = self.queue.dispatched.pop(0)
            if dispatch.delay:
                self.clock.advance(dispatch.delay)
            results.append(await self.run(dispatch.job_id, worker_id))
        return results

    def version(self, document: Document) -> DocumentVersion:
        """The document's *current* version, as stored now."""
        (current,) = (
            version
            for version in self.uow_factory.state.versions.values()
            if version.document_id == document.id and version.is_current
        )
        return current

    def jobs(self, document: Document) -> list[ProcessingJob]:
        version = self.version(document)
        return sorted(
            (
                job
                for job in self.uow_factory.state.jobs.values()
                if job.document_version_id == version.id
            ),
            key=lambda job: job.attempt,
        )

    def chunks(self, document: Document) -> list[StoredChunk]:
        return self.uow_factory.state.chunks.get(self.version(document).id, [])


async def build_pipeline(
    *,
    max_attempts: int = 5,
    base_delay: timedelta = timedelta(seconds=10),
    limits: ParseLimits | None = None,
    embedder: ControllableEmbedder | None = None,
) -> Pipeline:
    uow_factory = FakeUnitOfWorkFactory()
    embedder = embedder or ControllableEmbedder()
    uow_factory.state.controls.column_dimensions = embedder.space.dimensions
    clock = FixedClock()
    uow_factory.state.controls.clock_now = clock.now
    storage = FakeObjectStorage()
    queue = RecordingJobQueue()
    settings = build_settings()
    policy = ProcessingPolicy(
        retry=RetryPolicy(
            max_attempts=max_attempts,
            base_delay=base_delay,
            max_delay=timedelta(minutes=5),
        ),
        limits=limits or ParseLimits(),
        lease=LEASE,
        redelivery_grace=timedelta(minutes=10),
        random=lambda: 0.5,
    )
    lifecycle = JobLifecycle(uow_factory, queue, clock, policy)
    stages = PipelineStages(
        parsers=default_parser_registry(),
        normalizer=DocumentNormalizer(),
        chunker=StructureAwareChunker(),
        embedding=ChunkEmbedder(uow_factory, embedder, clock, PROCESSING_SYSTEM),
    )
    guard = AuthRateLimitGuard(
        InMemoryRateLimiter(), RateLimitPolicy.for_upload(settings), RecordingAuditSink()
    )

    async with uow_factory() as uow:
        user = await uow.users.create(
            email="owner@example.com", password_hash="h", full_name="Owner"
        )
        await uow.commit()
    workspace = await CreateWorkspace(uow_factory).execute(name="Acme", created_by_user_id=user.id)

    return Pipeline(
        uow_factory=uow_factory,
        storage=storage,
        queue=queue,
        clock=clock,
        policy=policy,
        embedder=embedder,
        lifecycle=lifecycle,
        worker=ProcessDocumentJob(lifecycle, storage, stages, PipelineFailureClassifier(), policy),
        recovery=RecoverStalledJobs(uow_factory, lifecycle, clock, policy),
        upload_document=UploadDocument(uow_factory, storage, settings, guard, queue),
        add_version=AddDocumentVersion(uow_factory, storage, settings, guard, queue),
        reprocess=ReprocessDocument(uow_factory, queue),
        status=GetProcessingStatus(uow_factory),
        ctx=AccessContext(user_id=user.id, workspace_id=workspace.id, role=Role.OWNER),
    )
