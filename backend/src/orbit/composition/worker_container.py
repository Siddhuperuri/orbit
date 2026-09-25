"""The worker's composition root.

Builds the processing pipeline from `Settings`: the same ports the tests wire
with fakes, bound here to PostgreSQL, S3, pypdf, markdown-it, and the
configured embedding provider (`composition/embeddings.py`).

A container is built **per task execution** and closed afterwards. Each task
runs its coroutine on a fresh event loop (`asyncio.run`), and asyncpg
connections and httpx clients are bound to the loop that created them; reusing
either across loops fails on the second task. Construction is cheap -- every
client connects lazily -- and a document task is seconds to minutes long, so
the per-task cost is noise. It also means nothing survives from one document to
the next that a hostile document could have corrupted.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from celery import Celery

from orbit.application.embeddings.embed_chunks import ChunkEmbedder
from orbit.application.embeddings.reindex import GetIndexCoverage, ReindexEmbeddings
from orbit.application.processing.lifecycle import PROCESSING_SYSTEM, JobLifecycle
from orbit.application.processing.policy import ProcessingPolicy
from orbit.application.processing.process_document import PipelineStages, ProcessDocumentJob
from orbit.application.processing.recover_stalled_jobs import RecoverStalledJobs
from orbit.composition.embeddings import EmbeddingBinding, build_embedding_provider
from orbit.core.clock import Clock, SystemClock
from orbit.core.config import Settings
from orbit.core.logging import get_logger
from orbit.domain.ports.processing import ProcessingJobQueue
from orbit.infrastructure.chunking.structure_aware import StructureAwareChunker
from orbit.infrastructure.db.session import Database
from orbit.infrastructure.db.unit_of_work import make_unit_of_work_factory
from orbit.infrastructure.parsing.normalization import DocumentNormalizer
from orbit.infrastructure.parsing.registry import default_parser_registry
from orbit.infrastructure.processing.failure_classifier import PipelineFailureClassifier
from orbit.infrastructure.queue.dispatcher import CeleryProcessingJobQueue
from orbit.infrastructure.storage.s3 import ObjectStorageClient

logger = get_logger(__name__)


@dataclass(slots=True)
class WorkerContainer:
    database: Database
    storage: ObjectStorageClient
    embeddings: EmbeddingBinding
    process_document: ProcessDocumentJob
    recover_stalled_jobs: RecoverStalledJobs
    lifecycle: JobLifecycle
    index_coverage: GetIndexCoverage
    reindex_embeddings: ReindexEmbeddings

    @classmethod
    def create(
        cls,
        settings: Settings,
        celery_app: Celery,
        *,
        clock: Clock | None = None,
        policy: ProcessingPolicy | None = None,
        queue: ProcessingJobQueue | None = None,
    ) -> WorkerContainer:
        clock = clock or SystemClock()
        policy = policy or ProcessingPolicy.from_settings(settings)
        # The worker's statements are long by design -- re-index batches and
        # the recovery sweep -- so it gets its own, far higher, server-side
        # ceiling rather than the API's request-shaped one.
        database = Database(
            settings, statement_timeout_seconds=settings.worker_db_statement_timeout_seconds
        )
        storage = ObjectStorageClient(settings)
        uow_factory = make_unit_of_work_factory(database, cursor_secret=settings.secret_key)
        queue = queue or CeleryProcessingJobQueue(celery_app)
        embeddings = build_embedding_provider(settings)

        lifecycle = JobLifecycle(uow_factory, queue, clock, policy)
        stages = PipelineStages(
            parsers=default_parser_registry(),
            normalizer=DocumentNormalizer(),
            chunker=StructureAwareChunker(),
            embedding=ChunkEmbedder(uow_factory, embeddings.provider, clock, PROCESSING_SYSTEM),
        )
        return cls(
            database=database,
            storage=storage,
            embeddings=embeddings,
            lifecycle=lifecycle,
            index_coverage=GetIndexCoverage(uow_factory, embeddings.provider),
            reindex_embeddings=ReindexEmbeddings(
                uow_factory,
                embeddings.provider,
                clock,
                batch_size=settings.embedding_reindex_batch_size,
            ),
            process_document=ProcessDocumentJob(
                lifecycle, storage, stages, PipelineFailureClassifier(), policy
            ),
            recover_stalled_jobs=RecoverStalledJobs(uow_factory, lifecycle, clock, policy),
        )

    async def aclose(self) -> None:
        closers: list[tuple[str, Callable[[], Awaitable[None]]]] = []
        closers.append(("embeddings", self.embeddings.aclose))
        closers.append(("database", self.database.dispose))
        for name, close in closers:
            try:
                await close()
            except Exception:
                logger.exception("worker.resource_close_failed", resource=name)
        try:
            self.storage.close()
        except Exception:
            logger.exception("worker.resource_close_failed", resource="storage")
