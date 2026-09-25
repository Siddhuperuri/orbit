"""The processing pipeline against real PostgreSQL and real MinIO.

The in-memory suite proves the logic; this proves the parts only a real
database can: `FOR UPDATE SKIP LOCKED` under genuine concurrency, the fenced
`UPDATE ... WHERE worker_id = :me` statements, the one-active-job partial
unique index, the check constraints that make ambiguous job states
unrepresentable, pgvector accepting the embeddings, the generated search
vector, and a crash mid-transaction leaving no partial chunks.

Unlike the rest of the integration suite, these tests **commit**: the pipeline
opens many independent transactions, exactly as a worker does. Each test gets
its own workspace, removed (with every row that cascades from it) afterwards.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from orbit.application.auth.rate_limits import AuthRateLimitGuard, RateLimitPolicy
from orbit.application.documents.add_document_version import AddDocumentVersion
from orbit.application.documents.upload_document import UploadDocument
from orbit.application.embeddings.embed_chunks import ChunkEmbedder
from orbit.application.embeddings.reindex import ReindexEmbeddings
from orbit.application.processing.lifecycle import (
    PROCESSING_SYSTEM,
    JobLifecycle,
    JobOutcome,
    JobRunResult,
)
from orbit.application.processing.policy import ProcessingPolicy
from orbit.application.processing.process_document import PipelineStages, ProcessDocumentJob
from orbit.application.processing.recover_stalled_jobs import RecoverStalledJobs
from orbit.application.processing.reprocess_document import ReprocessDocument
from orbit.application.retrieval.hybrid_search import HybridSearch, SearchPolicy
from orbit.core.clock import FixedClock
from orbit.core.config import Settings
from orbit.core.storage_keys import workspace_prefix
from orbit.domain.access import AccessContext, Role
from orbit.domain.errors import ConflictError
from orbit.domain.models.entities import Document
from orbit.domain.processing.retry import RetryPolicy
from orbit.domain.retrieval import RetrievalMethod, RetrievalMode, SearchQuery
from orbit.infrastructure.ai.fake_embeddings import FakeEmbeddingProvider
from orbit.infrastructure.chunking.structure_aware import StructureAwareChunker
from orbit.infrastructure.db.repositories.processing import SqlProcessingRepository
from orbit.infrastructure.db.session import Database
from orbit.infrastructure.db.unit_of_work import make_unit_of_work_factory
from orbit.infrastructure.parsing.normalization import DocumentNormalizer
from orbit.infrastructure.parsing.registry import default_parser_registry
from orbit.infrastructure.processing.failure_classifier import PipelineFailureClassifier
from orbit.infrastructure.storage.s3 import ObjectStorageClient
from tests.conftest import build_settings
from tests.fixtures.pdf import build_pdf, paragraph_lines
from tests.unit.fakes.fake_processing import RecordingJobQueue
from tests.unit.fakes.security_doubles import InMemoryRateLimiter, RecordingAuditSink
from tests.unit.processing.harness import ControllableEmbedder, WorkerKilledError

pytestmark = pytest.mark.integration

REAL_PDF = pathlib.Path(__file__).parents[1] / "fixtures" / "documents" / "employee-handbook.pdf"
LEASE = timedelta(minutes=5)


def _stack_settings() -> Settings:
    required = ("ORBIT_TEST_DATABASE_URL", "ORBIT_S3_BUCKET", "ORBIT_S3_ACCESS_KEY_ID")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.skip(f"not configured for integration: missing {', '.join(missing)}")
    return build_settings(
        database_url=os.environ["ORBIT_TEST_DATABASE_URL"],
        s3_bucket=os.environ["ORBIT_S3_BUCKET"],
        s3_endpoint_url=os.environ.get("ORBIT_S3_ENDPOINT_URL", "http://localhost:9000"),
        s3_access_key_id=os.environ["ORBIT_S3_ACCESS_KEY_ID"],
        s3_secret_access_key=os.environ["ORBIT_S3_SECRET_ACCESS_KEY"],
    )


@dataclass
class Worker:
    """One worker process: its own engine and connection pool."""

    database: Database
    lifecycle: JobLifecycle
    pipeline: ProcessDocumentJob
    recovery: RecoverStalledJobs
    embedder: ControllableEmbedder

    async def run(self, job_id: uuid.UUID, worker_id: str) -> JobRunResult:
        return await self.pipeline.execute(job_id, worker_id=worker_id)


@dataclass
class Stack:
    settings: Settings
    engine: AsyncEngine
    storage: ObjectStorageClient
    queue: RecordingJobQueue
    clock: FixedClock
    ctx: AccessContext
    api_database: Database
    upload: UploadDocument
    add_version: AddDocumentVersion
    workers: list[Worker]

    def start_worker(self, *, max_attempts: int = 5) -> Worker:
        """A fresh worker -- a new process, as far as the database can tell."""
        database = Database(self.settings)
        uow_factory = make_unit_of_work_factory(database, cursor_secret="integration")
        policy = ProcessingPolicy(
            retry=RetryPolicy(max_attempts=max_attempts, base_delay=timedelta(seconds=1)),
            lease=LEASE,
            random=lambda: 0.5,
        )
        embedder = ControllableEmbedder(
            inner=FakeEmbeddingProvider(dimensions=self.settings.embedding_dimensions)
        )
        lifecycle = JobLifecycle(uow_factory, self.queue, self.clock, policy)
        worker = Worker(
            database=database,
            lifecycle=lifecycle,
            pipeline=ProcessDocumentJob(
                lifecycle,
                self.storage,
                PipelineStages(
                    parsers=default_parser_registry(),
                    normalizer=DocumentNormalizer(),
                    chunker=StructureAwareChunker(),
                    embedding=ChunkEmbedder(uow_factory, embedder, self.clock, PROCESSING_SYSTEM),
                ),
                PipelineFailureClassifier(),
                policy,
            ),
            recovery=RecoverStalledJobs(uow_factory, lifecycle, self.clock, policy),
            embedder=embedder,
        )
        self.workers.append(worker)
        return worker

    async def upload_bytes(self, filename: str, data: bytes) -> Document:
        async def stream() -> AsyncIterator[bytes]:
            yield data

        result = await self.upload.execute(
            self.ctx, filename=filename, title=None, folder_id=None, content_stream=stream()
        )
        return result.document

    async def scalar(self, sql: str, **params: object) -> Any:  # noqa: ANN401 -- raw SQL
        async with self.engine.connect() as conn:
            return await conn.scalar(text(sql), params)

    async def rows(self, sql: str, **params: object) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            result = await conn.execute(text(sql), params)
            return [dict(row._mapping) for row in result]

    async def version(self, document: Document) -> dict[str, Any]:
        (row,) = await self.rows(
            "SELECT * FROM document_versions WHERE document_id = :d AND is_current", d=document.id
        )
        return row

    async def jobs(self, document: Document) -> list[dict[str, Any]]:
        version = await self.version(document)
        return await self.rows(
            "SELECT * FROM document_processing_jobs"
            " WHERE document_version_id = :v ORDER BY attempt",
            v=version["id"],
        )


@pytest.fixture
async def stack(migrated_engine: AsyncEngine) -> AsyncIterator[Stack]:
    settings = _stack_settings()
    storage = ObjectStorageClient(settings)
    api_database = Database(settings)
    uow_factory = make_unit_of_work_factory(api_database, cursor_secret="integration")
    queue = RecordingJobQueue()

    async with uow_factory() as uow:
        user = await uow.users.create(
            email=f"pipeline-{uuid.uuid4().hex[:10]}@example.test",
            password_hash="$argon2id$fake",
            full_name="Pipeline",
        )
        workspace = await uow.workspaces.create(
            name="Pipeline", slug=f"pipeline-{uuid.uuid4().hex[:10]}", created_by_user_id=user.id
        )
        await uow.memberships.add_owner(workspace.id, user.id)
        await uow.commit()

    guard = AuthRateLimitGuard(
        InMemoryRateLimiter(), RateLimitPolicy.for_upload(settings), RecordingAuditSink()
    )
    built = Stack(
        settings=settings,
        engine=migrated_engine,
        storage=storage,
        queue=queue,
        clock=FixedClock(datetime.now(UTC)),
        ctx=AccessContext(user_id=user.id, workspace_id=workspace.id, role=Role.OWNER),
        api_database=api_database,
        upload=UploadDocument(uow_factory, storage, settings, guard, queue),
        add_version=AddDocumentVersion(uow_factory, storage, settings, guard, queue),
        workers=[],
    )
    try:
        yield built
    finally:
        async with migrated_engine.begin() as conn:
            await conn.execute(text("DELETE FROM workspaces WHERE id = :w"), {"w": workspace.id})
            await conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": user.id})
        async for summary in storage.list_keys(workspace_prefix(workspace.id)):
            await storage.delete(summary.key)
        for worker in built.workers:
            await worker.database.dispose()
        await api_database.dispose()
        storage.close()


def _prose_pdf() -> bytes:
    prose = "Leases expire when a worker dies. Recovery schedules the next attempt. " * 30
    return build_pdf(
        [["1 Recovery", *paragraph_lines(prose)], ["2 Fencing", *paragraph_lines(prose)]]
    )


# ---------------------------------------------------------------------------


class TestRealPdfToReady:
    async def test_a_real_pdf_moves_from_upload_to_ready_with_indexed_chunks(
        self, stack: Stack
    ) -> None:
        document = await stack.upload_bytes("employee-handbook.pdf", REAL_PDF.read_bytes())
        assert (await stack.version(document))["status"] == "pending"
        (job,) = await stack.jobs(document)
        assert (job["status"], job["scheduled_for"] is not None) == ("queued", True)

        worker = stack.start_worker()
        (dispatch,) = stack.queue.take()
        result = await worker.run(dispatch.job_id, worker_id="worker-1")

        assert result.outcome is JobOutcome.SUCCEEDED
        version = await stack.version(document)
        assert (version["status"], version["page_count"]) == ("ready", 2)
        assert version["processed_at"] is not None
        chunks = await stack.rows(
            "SELECT ordinal, content, content_sha256, token_count, page_from, page_to,"
            " heading_path,"
            " vector_dims(embedding) AS dims, embedding_model, chunker_version,"
            " search_vector @@ plainto_tsquery('english', 'parental leave') AS matches_leave"
            " FROM chunks WHERE document_version_id = :v ORDER BY ordinal",
            v=version["id"],
        )
        assert len(chunks) == version["chunk_count"] > 0
        assert [c["ordinal"] for c in chunks] == list(range(len(chunks)))
        assert {c["dims"] for c in chunks} == {1536}
        assert {c["chunker_version"] for c in chunks} == {"sa1-512-768-64-32"}
        assert any(c["matches_leave"] for c in chunks), "the generated search vector is populated"
        headings = {c["heading_path"] for c in chunks}
        assert "2 Leave and Time Off" in headings and "4 Incident Response" in headings
        leave = next(c for c in chunks if c["heading_path"] == "2 Leave and Time Off")
        assert "Parental leave is sixteen weeks" in str(leave["content"])
        assert (leave["page_from"], leave["page_to"]) == (1, 1)
        (job,) = await stack.jobs(document)
        assert (job["status"], job["stage"], job["worker_id"], job["lease_expires_at"]) == (
            "succeeded",
            "done",
            None,
            None,
        )

    async def test_a_redelivered_message_leaves_the_indexed_document_untouched(
        self, stack: Stack
    ) -> None:
        document = await stack.upload_bytes("handbook.pdf", REAL_PDF.read_bytes())
        worker = stack.start_worker()
        (dispatch,) = stack.queue.take()
        await worker.run(dispatch.job_id, worker_id="worker-1")
        before = await stack.rows(
            "SELECT id, content_sha256 FROM chunks WHERE document_id = :d ORDER BY ordinal",
            d=document.id,
        )

        again = await stack.start_worker().run(dispatch.job_id, worker_id="worker-2")

        assert (again.outcome, again.reason) == (JobOutcome.SKIPPED, "already_finished")
        after = await stack.rows(
            "SELECT id, content_sha256 FROM chunks WHERE document_id = :d ORDER BY ordinal",
            d=document.id,
        )
        assert after == before, "not even the chunk ids changed"


class TestConcurrencyAndFencing:
    async def test_two_workers_racing_for_one_job_process_it_once(self, stack: Stack) -> None:
        document = await stack.upload_bytes("race.pdf", _prose_pdf())
        (dispatch,) = stack.queue.take()
        first, second = stack.start_worker(), stack.start_worker()

        results = await asyncio.gather(
            first.run(dispatch.job_id, worker_id="worker-1"),
            second.run(dispatch.job_id, worker_id="worker-2"),
        )

        outcomes = sorted(result.outcome.value for result in results)
        assert outcomes == ["skipped", "succeeded"]
        assert first.embedder.calls + second.embedder.calls == 1
        version = await stack.version(document)
        count = await stack.scalar(
            "SELECT count(*) FROM chunks WHERE document_version_id = :v", v=version["id"]
        )
        assert count == version["chunk_count"]

    async def test_a_restarted_worker_recovers_a_job_killed_mid_embedding(
        self, stack: Stack
    ) -> None:
        document = await stack.upload_bytes("crash.pdf", _prose_pdf())
        (dispatch,) = stack.queue.take()
        doomed = stack.start_worker()
        doomed.embedder.failures.append(WorkerKilledError())
        with pytest.raises(WorkerKilledError):
            await doomed.run(dispatch.job_id, worker_id="worker-doomed")
        await doomed.database.dispose()  # the process is gone, and so are its connections

        (job,) = await stack.jobs(document)
        assert (job["status"], job["stage"]) == ("running", "embed")
        assert (await stack.version(document))["status"] == "processing"
        assert (
            await stack.scalar("SELECT count(*) FROM chunks WHERE document_id = :d", d=document.id)
            == 0
        )

        restarted = stack.start_worker()
        assert (await restarted.recovery.execute()).abandoned_found == 0, "lease still live"

        stack.clock.advance(LEASE + timedelta(seconds=1))
        report = await restarted.recovery.execute()
        assert (report.abandoned_found, report.abandoned_recovered) == (1, 1)
        (retry,) = stack.queue.take()
        stack.clock.advance(retry.delay)
        assert (
            await restarted.run(retry.job_id, worker_id="worker-restarted")
        ).outcome is JobOutcome.SUCCEEDED

        version = await stack.version(document)
        assert version["status"] == "ready"
        lost, succeeded = await stack.jobs(document)
        assert (lost["status"], lost["error_code"], lost["failure_kind"]) == (
            "failed",
            "WORKER_LOST",
            "transient",
        )
        assert (succeeded["status"], succeeded["run_attempt"]) == ("succeeded", 2)
        ordinals = await stack.rows(
            "SELECT ordinal FROM chunks WHERE document_version_id = :v ORDER BY ordinal",
            v=version["id"],
        )
        assert [row["ordinal"] for row in ordinals] == list(range(version["chunk_count"]))

    async def test_a_crash_inside_the_index_transaction_writes_no_chunks(
        self, stack: Stack, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        document = await stack.upload_bytes("partial.pdf", _prose_pdf())
        (dispatch,) = stack.queue.take()
        original = SqlProcessingRepository.replace_chunks

        async def insert_then_die(self, *args, **kwargs):  # type: ignore[no-untyped-def]  # noqa: ANN001, ANN002, ANN003, ANN202
            await original(self, *args, **kwargs)
            inside = await self._session.scalar(
                text("SELECT count(*) FROM chunks WHERE document_id = :d"), {"d": document.id}
            )
            assert inside > 0, "rows really were written inside the transaction"
            raise WorkerKilledError

        monkeypatch.setattr(SqlProcessingRepository, "replace_chunks", insert_then_die)
        doomed = stack.start_worker()
        with pytest.raises(WorkerKilledError):
            await doomed.run(dispatch.job_id, worker_id="worker-doomed")
        monkeypatch.setattr(SqlProcessingRepository, "replace_chunks", original)

        assert (
            await stack.scalar("SELECT count(*) FROM chunks WHERE document_id = :d", d=document.id)
            == 0
        )
        (job,) = await stack.jobs(document)
        assert job["status"] == "running", "the fenced completion rolled back with the chunks"

        stack.clock.advance(LEASE + timedelta(seconds=1))
        worker = stack.start_worker()
        await worker.recovery.execute()
        (retry,) = stack.queue.take()
        stack.clock.advance(retry.delay)
        await worker.run(retry.job_id, worker_id="worker-2")
        version = await stack.version(document)
        assert version["status"] == "ready"
        count = await stack.scalar(
            "SELECT count(*) FROM chunks WHERE document_version_id = :v", v=version["id"]
        )
        assert count == version["chunk_count"]

    async def test_a_zombie_worker_cannot_overwrite_the_worker_that_replaced_it(
        self, stack: Stack
    ) -> None:
        document = await stack.upload_bytes("zombie.pdf", _prose_pdf())
        (dispatch,) = stack.queue.take()
        zombie, successor = stack.start_worker(), stack.start_worker()
        successor_results: list[JobRunResult] = []

        async def stall_while_replaced() -> None:
            stack.clock.advance(LEASE + timedelta(seconds=1))
            await successor.recovery.execute()
            (retry,) = stack.queue.take()
            stack.clock.advance(retry.delay)
            successor_results.append(
                await successor.run(retry.job_id, worker_id="worker-successor")
            )

        zombie.embedder.before_return = stall_while_replaced
        zombie_result = await zombie.run(dispatch.job_id, worker_id="worker-zombie")

        assert [r.outcome for r in successor_results] == [JobOutcome.SUCCEEDED]
        assert zombie_result.outcome is JobOutcome.LEASE_LOST
        first, second = await stack.jobs(document)
        assert first["error_code"] == "WORKER_LOST"
        assert second["status"] == "succeeded"
        version = await stack.version(document)
        count = await stack.scalar(
            "SELECT count(*) FROM chunks WHERE document_version_id = :v", v=version["id"]
        )
        assert version["status"] == "ready" and count == version["chunk_count"]

    async def test_a_version_replaced_mid_run_is_not_indexed(self, stack: Stack) -> None:
        document = await stack.upload_bytes("v1.txt", b"The first revision says one thing. " * 40)
        v1 = await stack.version(document)
        (dispatch,) = stack.queue.take()
        worker = stack.start_worker()

        async def replace_document() -> None:
            async def stream() -> AsyncIterator[bytes]:
                yield b"The second revision says another thing entirely. " * 40

            await stack.add_version.execute(
                stack.ctx, document.id, filename="v2.txt", content_stream=stream()
            )

        worker.embedder.before_return = replace_document
        result = await worker.run(dispatch.job_id, worker_id="worker-1")

        assert (result.outcome, result.reason) == (JobOutcome.FAILED, "DOCUMENT_VERSION_SUPERSEDED")
        (old,) = await stack.rows(
            "SELECT status, failure_code FROM document_versions WHERE id = :v", v=v1["id"]
        )
        assert old == {"status": "failed", "failure_code": "DOCUMENT_VERSION_SUPERSEDED"}
        assert (
            await stack.scalar(
                "SELECT count(*) FROM chunks WHERE document_version_id = :v", v=v1["id"]
            )
            == 0
        )

        (v2_dispatch,) = stack.queue.take()
        assert (
            await worker.run(v2_dispatch.job_id, worker_id="worker-1")
        ).outcome is JobOutcome.SUCCEEDED
        assert (await stack.version(document))["status"] == "ready"


class TestFailuresPersist:
    async def test_a_corrupt_pdf_fails_permanently_with_both_messages_stored(
        self, stack: Stack
    ) -> None:
        document = await stack.upload_bytes("broken.pdf", b"%PDF-1.7\n" + b"\x00junk" * 200)
        (dispatch,) = stack.queue.take()
        await stack.start_worker().run(dispatch.job_id, worker_id="worker-1")

        version = await stack.version(document)
        assert (version["status"], version["failure_code"]) == ("failed", "DOCUMENT_CORRUPT")
        assert "damaged" in str(version["failure_reason"])
        (job,) = await stack.jobs(document)
        assert (job["failure_kind"], job["error_code"]) == ("permanent", "DOCUMENT_CORRUPT")
        assert job["error_message"], "operator detail recorded"
        assert stack.queue.take() == []

    async def test_a_transient_failure_persists_a_scheduled_retry(self, stack: Stack) -> None:
        from orbit.domain.errors import AIProviderUnavailableError  # noqa: PLC0415

        document = await stack.upload_bytes("retry.txt", b"Retry me later. " * 50)
        (dispatch,) = stack.queue.take()
        worker = stack.start_worker()
        worker.embedder.failures.append(AIProviderUnavailableError("503"))

        result = await worker.run(dispatch.job_id, worker_id="worker-1")

        assert result.outcome is JobOutcome.RETRY_SCHEDULED
        assert (await stack.version(document))["status"] == "pending"
        failed, queued = await stack.jobs(document)
        assert (failed["status"], failed["failure_kind"]) == ("failed", "transient")
        assert (queued["status"], queued["attempt"], queued["run_attempt"]) == ("queued", 2, 2)
        assert queued["scheduled_for"] > stack.clock.now()
        assert queued["request_id"] == failed["request_id"]


class TestSchemaGuarantees:
    async def test_only_one_active_job_per_version(self, stack: Stack) -> None:
        document = await stack.upload_bytes("one.txt", b"only one job at a time")
        version = await stack.version(document)
        uow_factory = make_unit_of_work_factory(stack.api_database, cursor_secret="integration")
        async with uow_factory() as uow:
            with pytest.raises(ConflictError, match="already being processed"):
                await uow.processing.create_initial_job(stack.ctx, version["id"], request_id=None)

    @pytest.mark.parametrize(
        "update",
        [
            pytest.param("status = 'running'", id="running-without-a-lease"),
            pytest.param(
                "status = 'failed', started_at = now(), finished_at = now()",
                id="failed-without-a-kind",
            ),
            pytest.param("run_attempt = 5", id="run-attempt-beyond-attempt"),
        ],
    )
    async def test_ambiguous_job_states_are_unrepresentable(
        self, stack: Stack, update: str
    ) -> None:
        document = await stack.upload_bytes(
            f"{uuid.uuid4().hex}.txt", uuid.uuid4().hex.encode() * 10
        )
        (job,) = await stack.jobs(document)
        with pytest.raises(IntegrityError):
            async with stack.engine.begin() as conn:
                await conn.execute(
                    text(f"UPDATE document_processing_jobs SET {update} WHERE id = :j"),  # noqa: S608
                    {"j": job["id"]},
                )

    async def test_recovery_republishes_a_lost_message_once_per_grace_period(
        self, stack: Stack
    ) -> None:
        stack.queue.fail = True
        document = await stack.upload_bytes("lost.txt", b"the broker lost this message " * 20)
        stack.queue.fail = False
        worker = stack.start_worker()

        stack.clock.advance(timedelta(minutes=11))
        first = await worker.recovery.execute()
        second = await worker.recovery.execute()

        assert (first.redelivered, second.redelivered) >= (1, 0)
        assert second.redelivered == 0
        mine = [d for d in stack.queue.take() if d.job_id == (await stack.jobs(document))[0]["id"]]
        assert len(mine) == 1
        assert (
            await worker.run(mine[0].job_id, worker_id="worker-1")
        ).outcome is JobOutcome.SUCCEEDED


class TestVectorIndexEndToEnd:
    """Embedding and dense search through the real pipeline, database, and store."""

    async def test_a_ready_document_is_searchable_and_every_chunk_is_fully_described(
        self, stack: Stack
    ) -> None:
        document = await stack.upload_bytes("handbook.pdf", REAL_PDF.read_bytes())
        worker = stack.start_worker()
        (dispatch,) = stack.queue.take()
        assert (await worker.run(dispatch.job_id, "worker-1")).outcome is JobOutcome.SUCCEEDED

        version = await stack.version(document)
        described = await stack.scalar(
            "SELECT count(*) FROM chunks WHERE document_version_id = :v"
            " AND embedding_model = :m AND embedding_dimensions = vector_dims(embedding)"
            " AND length(embedding_input_sha256) = 64 AND embedded_at IS NOT NULL",
            v=version["id"],
            m=worker.embedder.space.model,
        )
        assert described == version["chunk_count"] > 0

        uow_factory = make_unit_of_work_factory(stack.api_database, cursor_secret="integration")
        response = await HybridSearch(uow_factory, worker.embedder, SearchPolicy()).execute(
            stack.ctx, SearchQuery(text="how long is parental leave", limit=5)
        )
        hits = response.results
        assert response.retrievers == (RetrievalMethod.LEXICAL, RetrievalMethod.SEMANTIC)
        assert hits and hits[0].document.id == document.id
        assert "Parental leave" in hits[0].chunk.text

    async def test_reprocessing_an_indexed_document_calls_the_provider_zero_times(
        self, stack: Stack
    ) -> None:
        document = await stack.upload_bytes("reuse.pdf", _prose_pdf())
        worker = stack.start_worker()
        (dispatch,) = stack.queue.take()
        await worker.run(dispatch.job_id, "worker-1")
        version = await stack.version(document)
        before = await stack.rows(
            "SELECT embedding_input_sha256, embedded_at FROM chunks"
            " WHERE document_version_id = :v ORDER BY ordinal",
            v=version["id"],
        )
        async with stack.engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE document_versions SET status = 'failed', failure_code = 'X',"
                    " failure_reason = 'x' WHERE id = :v"
                ),
                {"v": version["id"]},
            )
        uow_factory = make_unit_of_work_factory(stack.api_database, cursor_secret="integration")
        await ReprocessDocument(uow_factory, stack.queue).execute(stack.ctx, document.id)
        fresh = stack.start_worker()
        (again,) = stack.queue.take()

        assert (await fresh.run(again.job_id, "worker-2")).outcome is JobOutcome.SUCCEEDED
        assert fresh.embedder.calls == 0, "every vector came from the index"
        after = await stack.rows(
            "SELECT embedding_input_sha256, embedded_at FROM chunks"
            " WHERE document_version_id = :v ORDER BY ordinal",
            v=version["id"],
        )
        assert after == before, "same inputs, and reused vectors keep their creation time"

    async def test_a_model_change_is_excluded_from_search_until_reindexed_in_place(
        self, stack: Stack
    ) -> None:
        document = await stack.upload_bytes("model.pdf", REAL_PDF.read_bytes())
        worker = stack.start_worker()
        (dispatch,) = stack.queue.take()
        await worker.run(dispatch.job_id, "worker-1")
        version = await stack.version(document)
        ids_before = await stack.rows(
            "SELECT id, content FROM chunks WHERE document_version_id = :v ORDER BY ordinal",
            v=version["id"],
        )
        uow_factory = make_unit_of_work_factory(stack.api_database, cursor_secret="integration")
        v2 = ControllableEmbedder(
            inner=FakeEmbeddingProvider(
                dimensions=stack.settings.embedding_dimensions, model_id="orbit-fake-embedding-v2"
            )
        )
        search_v2 = HybridSearch(uow_factory, v2, SearchPolicy())
        semantic_only = SearchQuery(text="parental leave", mode=RetrievalMode.SEMANTIC)
        assert (await search_v2.execute(stack.ctx, semantic_only)).results == ()

        # Other tests' workspaces may hold v1 chunks too; the re-index is
        # global by design, so it is asserted by effect on this document.
        report = await ReindexEmbeddings(uow_factory, v2, stack.clock, batch_size=7).execute()

        assert report.complete and report.inconsistent == 0
        spaces = await stack.rows(
            "SELECT DISTINCT embedding_model FROM chunks WHERE document_version_id = :v",
            v=version["id"],
        )
        assert spaces == [{"embedding_model": "orbit-fake-embedding-v2"}]
        assert (
            await stack.rows(
                "SELECT id, content FROM chunks WHERE document_version_id = :v ORDER BY ordinal",
                v=version["id"],
            )
            == ids_before
        )
        assert (await stack.version(document))["status"] == "ready"
        hits = (await search_v2.execute(stack.ctx, semantic_only)).results
        assert hits and hits[0].document.id == document.id
