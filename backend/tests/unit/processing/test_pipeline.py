"""The asynchronous pipeline end to end, on fakes: states, retries, failure
classes, idempotency, crash recovery, fencing, and supersession.

Each scenario runs the production use cases; only storage, the database, the
broker, and the clock are in memory.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from structlog.testing import capture_logs

from orbit.application.processing.lifecycle import JobOutcome
from orbit.domain.access import AccessContext, Role
from orbit.domain.errors import (
    AIProviderUnavailableError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    StorageUnavailableError,
)
from orbit.domain.models.entities import Document, ProcessingStatus
from orbit.domain.processing.content import ParseLimits
from orbit.domain.processing.failures import FailureCode, FailureKind
from orbit.domain.processing.jobs import JobStatus, PipelineStage
from tests.fixtures.pdf import build_pdf, encrypt_pdf, paragraph_lines
from tests.unit.processing.harness import (
    LEASE,
    Pipeline,
    WorkerKilledError,
    build_pipeline,
)

PROSE = (
    "Refresh tokens rotate on every use. A reused token revokes its whole family, "
    "which contains the damage a stolen token can do. Sessions expire after thirty days. "
)


def _pdf() -> bytes:
    return build_pdf(
        [
            ["1 Security", *paragraph_lines(PROSE * 6)],
            ["2 Operations", *paragraph_lines(PROSE * 6)],
        ]
    )


# ---------------------------------------------------------------------------
# Upload hands off; the worker processes
# ---------------------------------------------------------------------------


class TestHandOff:
    async def test_upload_records_pending_and_a_queued_job_without_processing(self) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload("report.pdf", _pdf())

        version = pipeline.version(document)
        assert version.status is ProcessingStatus.PENDING
        (job,) = pipeline.jobs(document)
        assert (job.status, job.attempt, job.run_attempt) == (JobStatus.QUEUED, 1, 1)
        assert pipeline.chunks(document) == []
        assert pipeline.embedder.calls == 0, "nothing is processed inside the upload request"
        assert [d.job_id for d in pipeline.queue.dispatched] == [job.id]

    async def test_a_deduplicated_upload_creates_no_second_job(self) -> None:
        pipeline = await build_pipeline()
        first = await pipeline.upload("a.txt", b"same content")
        second = await pipeline.upload("b.txt", b"same content")
        assert first.id == second.id
        assert len(pipeline.uow_factory.state.jobs) == 1
        assert len(pipeline.queue.dispatched) == 1

    async def test_a_broker_outage_does_not_fail_the_upload(self) -> None:
        pipeline = await build_pipeline()
        pipeline.queue.fail = True
        with capture_logs() as logs:
            document = await pipeline.upload("a.txt", b"survives the outage")
        assert pipeline.version(document).status is ProcessingStatus.PENDING
        assert pipeline.jobs(document)[0].status is JobStatus.QUEUED
        assert any(entry["event"] == "processing.enqueue_failed" for entry in logs)


class TestHappyPath:
    @pytest.mark.parametrize(
        ("filename", "payload", "pages"),
        [
            ("report.pdf", _pdf(), 2),
            ("guide.md", f"# Guide\n\n## Tokens\n\n{PROSE * 8}\n".encode(), None),
            ("notes.txt", (PROSE * 8).encode(), None),
        ],
    )
    async def test_every_format_moves_pending_processing_ready(
        self, filename: str, payload: bytes, pages: int | None
    ) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload(filename, payload)
        observed: list[ProcessingStatus] = []

        async def observe() -> None:
            observed.append(pipeline.version(document).status)

        pipeline.embedder.before_return = observe
        (job_id,) = pipeline.dispatched_job_ids()
        result = await pipeline.run(job_id)

        assert result.outcome is JobOutcome.SUCCEEDED
        assert observed == [ProcessingStatus.PROCESSING]
        version = pipeline.version(document)
        assert version.status is ProcessingStatus.READY
        assert version.chunk_count == len(pipeline.chunks(document)) > 0
        assert version.page_count == pages
        assert version.processed_at is not None
        (job,) = pipeline.jobs(document)
        assert (job.status, job.stage, job.worker_id) == (
            JobStatus.SUCCEEDED,
            PipelineStage.DONE,
            None,
        )

    async def test_chunks_record_provenance_and_versions(self) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload("report.pdf", _pdf())
        await pipeline.drain()
        chunks = pipeline.chunks(document)
        assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
        assert {chunk.chunker_version for chunk in chunks} == {"sa1-512-768-64-32"}
        assert {chunk.space for chunk in chunks} == {pipeline.embedder.space}
        assert chunks[0].page_start == 1 and chunks[-1].page_end == 2

    async def test_every_stage_is_logged_with_its_duration(self) -> None:
        pipeline = await build_pipeline()
        await pipeline.upload("report.pdf", _pdf())
        with capture_logs() as logs:
            await pipeline.drain()
        stages = [entry["stage"] for entry in logs if entry["event"] == "pipeline.stage_completed"]
        assert stages == ["fetch", "parse", "normalize", "chunk", "embed", "index"]
        assert all(
            "duration_ms" in entry for entry in logs if entry["event"] == "pipeline.stage_completed"
        )
        assert any(entry["event"] == "pipeline.completed" for entry in logs)


# ---------------------------------------------------------------------------
# Failure classes
# ---------------------------------------------------------------------------


class TestPermanentFailures:
    @pytest.mark.parametrize(
        ("filename", "payload", "code"),
        [
            ("broken.pdf", b"%PDF-1.7\n" + b"\x00garbage" * 50, "DOCUMENT_CORRUPT"),
            (
                "locked.pdf",
                encrypt_pdf(build_pdf([["x"]]), user_password="pw"),
                "DOCUMENT_ENCRYPTED",
            ),
            ("scan.pdf", build_pdf([[], [], []]), "DOCUMENT_NO_EXTRACTABLE_TEXT"),
            (
                "mostly-scan.pdf",
                build_pdf([["7"], [], ["stamp"], [], []]),
                "DOCUMENT_NO_EXTRACTABLE_TEXT",
            ),
            ("blank.txt", b" \n\n\t \n", "DOCUMENT_EMPTY"),
            ("blank.md", b"<!-- only a comment -->\n", "DOCUMENT_EMPTY"),
        ],
    )
    async def test_fail_on_the_first_attempt_with_a_user_safe_reason(
        self, filename: str, payload: bytes, code: str
    ) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload(filename, payload)
        results = await pipeline.drain()

        assert [result.outcome for result in results] == [JobOutcome.FAILED]
        version = pipeline.version(document)
        assert version.status is ProcessingStatus.FAILED
        assert version.failure_code == code
        assert version.failure_reason and "Traceback" not in version.failure_reason
        (job,) = pipeline.jobs(document)
        assert job.failure_kind is FailureKind.PERMANENT
        assert job.error_message, "operators get the detail"
        assert pipeline.queue.dispatched == [], "a permanent failure is never retried"
        assert pipeline.chunks(document) == []

    async def test_stored_bytes_that_do_not_match_the_upload_fail_integrity(self) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload("a.txt", b"original content")
        key = pipeline.version(document).storage_key
        pipeline.storage.objects[key] = dataclasses.replace(
            pipeline.storage.objects[key], data=b"tampered content"
        )
        await pipeline.drain()
        assert pipeline.version(document).failure_code == "DOCUMENT_INTEGRITY_FAILED"

    async def test_a_missing_stored_object_fails_without_retrying(self) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload("a.txt", b"will vanish")
        pipeline.storage.objects.clear()
        await pipeline.drain()
        assert pipeline.version(document).failure_code == "DOCUMENT_SOURCE_MISSING"
        assert len(pipeline.jobs(document)) == 1

    async def test_exceeding_the_chunk_limit_is_permanent(self) -> None:
        pipeline = await build_pipeline(limits=ParseLimits(max_chunks=1))
        document = await pipeline.upload("long.txt", (PROSE * 60).encode())
        await pipeline.drain()
        assert pipeline.version(document).failure_code == "DOCUMENT_LIMIT_EXCEEDED"


class TestDefects:
    async def test_an_unexpected_exception_fails_with_a_generic_reason_and_full_logs(self) -> None:
        pipeline = await build_pipeline()
        pipeline.embedder.failures.append(KeyError("internal_column_name"))
        document = await pipeline.upload("a.txt", PROSE.encode())
        with capture_logs() as logs:
            await pipeline.drain()

        version = pipeline.version(document)
        assert version.failure_code == FailureCode.INTERNAL
        assert "internal_column_name" not in (version.failure_reason or "")
        (job,) = pipeline.jobs(document)
        assert job.failure_kind is FailureKind.DEFECT
        assert "internal_column_name" in (job.error_message or "")
        failure_logs = [entry for entry in logs if entry["event"] == "pipeline.attempt_failed"]
        assert failure_logs and failure_logs[0]["log_level"] == "error"
        assert failure_logs[0].get("exc_info"), "defects are logged with their traceback"


class TestTransientFailuresAndRetries:
    async def test_a_dependency_outage_returns_to_pending_and_retries_with_backoff(self) -> None:
        pipeline = await build_pipeline()
        pipeline.embedder.failures.append(AIProviderUnavailableError("503 from provider"))
        document = await pipeline.upload("a.txt", (PROSE * 4).encode())
        (first_job_id,) = pipeline.dispatched_job_ids()

        result = await pipeline.run(first_job_id)

        assert result.outcome is JobOutcome.RETRY_SCHEDULED
        assert pipeline.version(document).status is ProcessingStatus.PENDING
        first, second = pipeline.jobs(document)
        assert (first.status, first.failure_kind, first.error_code) == (
            JobStatus.FAILED,
            FailureKind.TRANSIENT,
            "AI_PROVIDER_UNAVAILABLE",
        )
        assert (second.status, second.attempt, second.run_attempt) == (JobStatus.QUEUED, 2, 2)
        assert second.scheduled_for == pipeline.clock.now() + timedelta(seconds=7.5)
        (dispatch,) = pipeline.queue.dispatched
        assert dispatch.job_id == second.id and dispatch.delay == timedelta(seconds=7.5)

    async def test_a_retry_message_arriving_early_waits_for_its_backoff(self) -> None:
        # A two-minute backoff: well past the tolerance for a slightly early
        # broker countdown.
        pipeline = await build_pipeline(base_delay=timedelta(minutes=2))
        pipeline.embedder.failures.append(AIProviderUnavailableError("503"))
        document = await pipeline.upload("a.txt", (PROSE * 4).encode())
        await pipeline.run(pipeline.dispatched_job_ids()[0])
        (retry_id,) = pipeline.dispatched_job_ids()

        early = await pipeline.run(retry_id)  # clock not advanced
        assert (early.outcome, early.reason) == (JobOutcome.SKIPPED, "not_due")

        pipeline.clock.advance(timedelta(minutes=2))
        assert (await pipeline.run(retry_id)).outcome is JobOutcome.SUCCEEDED
        assert pipeline.version(document).status is ProcessingStatus.READY

    async def test_backoff_grows_between_attempts(self) -> None:
        pipeline = await build_pipeline(max_attempts=4)
        pipeline.embedder.failures.extend(AIProviderUnavailableError("503") for _ in range(3))
        await pipeline.upload("a.txt", (PROSE * 4).encode())
        delays = []
        for _ in range(3):
            dispatch = pipeline.queue.dispatched.pop(0)
            pipeline.clock.advance(dispatch.delay)
            await pipeline.run(dispatch.job_id)
            delays.append(pipeline.queue.dispatched[0].delay)
        assert delays == sorted(delays) and delays[0] < delays[-1]

    async def test_retries_stop_at_the_limit_and_the_reason_does_not_blame_the_file(self) -> None:
        pipeline = await build_pipeline(max_attempts=3)
        pipeline.embedder.failures.extend(AIProviderUnavailableError("503") for _ in range(10))
        document = await pipeline.upload("a.txt", (PROSE * 4).encode())

        results = await pipeline.drain()

        assert [r.outcome for r in results] == [
            JobOutcome.RETRY_SCHEDULED,
            JobOutcome.RETRY_SCHEDULED,
            JobOutcome.FAILED,
        ]
        version = pipeline.version(document)
        assert version.status is ProcessingStatus.FAILED
        assert version.failure_code == FailureCode.RETRIES_EXHAUSTED
        assert "Nothing is wrong with the file" in (version.failure_reason or "")
        assert [job.status for job in pipeline.jobs(document)] == [JobStatus.FAILED] * 3
        assert pipeline.embedder.calls == 3

    async def test_storage_outage_while_fetching_is_transient(self) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload("a.txt", PROSE.encode())

        def outage(key: str) -> object:
            raise StorageUnavailableError("down", key=key)

        pipeline.storage.open_stream = outage  # type: ignore[assignment,method-assign]
        result = await pipeline.run(pipeline.dispatched_job_ids()[0])
        assert result.outcome is JobOutcome.RETRY_SCHEDULED
        assert pipeline.version(document).status is ProcessingStatus.PENDING


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    async def test_a_redelivered_message_after_success_changes_nothing(self) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload("report.pdf", _pdf())
        (job_id,) = pipeline.dispatched_job_ids()
        await pipeline.run(job_id)
        chunks_before = pipeline.chunks(document)
        version_before = pipeline.version(document)

        again = await pipeline.run(job_id, worker_id="worker-b")

        assert (again.outcome, again.reason) == (JobOutcome.SKIPPED, "already_finished")
        assert pipeline.chunks(document) == chunks_before
        assert pipeline.version(document) == version_before
        assert pipeline.embedder.calls == 1

    async def test_a_duplicate_delivery_while_running_is_skipped(self) -> None:
        pipeline = await build_pipeline()
        await pipeline.upload("a.txt", (PROSE * 4).encode())
        (job_id,) = pipeline.dispatched_job_ids()
        duplicate = []

        async def deliver_again() -> None:
            duplicate.append(await pipeline.run(job_id, worker_id="worker-b"))

        pipeline.embedder.before_return = deliver_again
        assert (await pipeline.run(job_id)).outcome is JobOutcome.SUCCEEDED
        assert [(d.outcome, d.reason) for d in duplicate] == [
            (JobOutcome.SKIPPED, "held_elsewhere")
        ]

    async def test_a_job_locked_by_another_transaction_is_skipped_not_waited_on(self) -> None:
        pipeline = await build_pipeline()
        await pipeline.upload("a.txt", PROSE.encode())
        (job_id,) = pipeline.dispatched_job_ids()
        pipeline.uow_factory.state.controls.locked_job_ids.add(job_id)
        result = await pipeline.run(job_id)
        assert (result.outcome, result.reason) == (
            JobOutcome.SKIPPED,
            "locked_by_another_transaction",
        )

    async def test_an_unknown_job_is_skipped(self) -> None:
        pipeline = await build_pipeline()
        result = await pipeline.run(uuid.uuid4())
        assert (result.outcome, result.reason) == (JobOutcome.SKIPPED, "unknown_job")

    async def test_reprocessing_produces_identical_chunks(self) -> None:
        pipeline = await build_pipeline()
        pipeline.embedder.failures.append(ValueError("defect on first run"))
        document = await pipeline.upload("guide.md", f"# G\n\n{PROSE * 12}".encode())
        await pipeline.drain()
        assert pipeline.version(document).status is ProcessingStatus.FAILED

        await pipeline.reprocess.execute(pipeline.ctx, document.id)
        await pipeline.drain()
        first_run = [chunk.text for chunk in pipeline.chunks(document)]

        # Force a second full run of the same bytes and compare.
        version = pipeline.version(document)
        pipeline.uow_factory.state.versions[version.id] = dataclasses.replace(
            version, status=ProcessingStatus.FAILED, failure_code="X", failure_reason="x"
        )
        await pipeline.reprocess.execute(pipeline.ctx, document.id)
        await pipeline.drain()
        assert [chunk.text for chunk in pipeline.chunks(document)] == first_run

    async def test_a_second_active_job_for_one_version_is_impossible(self) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload("a.txt", PROSE.encode())
        version = pipeline.version(document)
        async with pipeline.uow_factory() as uow:
            with pytest.raises(ConflictError):
                await uow.processing.create_initial_job(pipeline.ctx, version.id, request_id=None)


# ---------------------------------------------------------------------------
# Worker restarts never corrupt state
# ---------------------------------------------------------------------------


class TestWorkerRestart:
    async def _kill_during_embedding(
        self, pipeline: Pipeline, filename: str = "report.pdf"
    ) -> tuple[Document, uuid.UUID]:
        pipeline.embedder.failures.append(WorkerKilledError())
        document = await pipeline.upload(filename, _pdf())
        (job_id,) = pipeline.dispatched_job_ids()
        with pytest.raises(WorkerKilledError):
            await pipeline.run(job_id)
        return document, job_id

    async def test_a_killed_worker_leaves_a_consistent_in_progress_state(self) -> None:
        pipeline = await build_pipeline()
        document, _ = await self._kill_during_embedding(pipeline)

        assert pipeline.version(document).status is ProcessingStatus.PROCESSING
        (job,) = pipeline.jobs(document)
        assert (job.status, job.stage, job.worker_id) == (
            JobStatus.RUNNING,
            PipelineStage.EMBED,
            "worker-a",
        )
        assert pipeline.chunks(document) == [], "nothing half-indexed"

    async def test_before_the_lease_expires_a_restarted_worker_does_not_steal_the_job(self) -> None:
        pipeline = await build_pipeline()
        document, job_id = await self._kill_during_embedding(pipeline)
        result = await pipeline.run(job_id, worker_id="worker-restarted")
        assert (result.outcome, result.reason) == (JobOutcome.SKIPPED, "held_elsewhere")
        report = await pipeline.recovery.execute()
        assert report.abandoned_found == 0
        assert pipeline.version(document).status is ProcessingStatus.PROCESSING

    async def test_recovery_after_the_lease_expires_retries_and_reaches_ready_exactly_once(
        self,
    ) -> None:
        pipeline = await build_pipeline()
        document, _ = await self._kill_during_embedding(pipeline)
        pipeline.clock.advance(LEASE + timedelta(seconds=1))

        report = await pipeline.recovery.execute()

        assert (report.abandoned_found, report.abandoned_recovered) == (1, 1)
        assert pipeline.version(document).status is ProcessingStatus.PENDING
        lost, retry = pipeline.jobs(document)
        assert (lost.status, lost.error_code, lost.failure_kind) == (
            JobStatus.FAILED,
            FailureCode.WORKER_LOST,
            FailureKind.TRANSIENT,
        )
        assert "embed" in (lost.error_message or "")
        assert retry.status is JobStatus.QUEUED

        results = await pipeline.drain(worker_id="worker-restarted")
        assert [r.outcome for r in results] == [JobOutcome.SUCCEEDED]
        version = pipeline.version(document)
        assert version.status is ProcessingStatus.READY
        chunks = pipeline.chunks(document)
        assert version.chunk_count == len(chunks)
        assert [c.ordinal for c in chunks] == list(range(len(chunks))), "no duplicate chunk set"

    async def test_the_redelivered_message_itself_triggers_recovery_after_expiry(self) -> None:
        pipeline = await build_pipeline()
        document, job_id = await self._kill_during_embedding(pipeline)
        pipeline.clock.advance(LEASE + timedelta(seconds=1))

        result = await pipeline.run(job_id, worker_id="worker-restarted")

        assert result.outcome is JobOutcome.RETRY_SCHEDULED
        await pipeline.drain(worker_id="worker-restarted")
        assert pipeline.version(document).status is ProcessingStatus.READY

    async def test_a_crash_mid_index_leaves_no_partial_chunks(self) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload("report.pdf", _pdf())
        pipeline.uow_factory.state.controls.fail_next_index = WorkerKilledError()
        with pytest.raises(WorkerKilledError):
            await pipeline.drain()

        assert pipeline.chunks(document) == [], "the index transaction rolled back"
        assert pipeline.version(document).status is ProcessingStatus.PROCESSING

        pipeline.clock.advance(LEASE + timedelta(seconds=1))
        await pipeline.recovery.execute()
        await pipeline.drain(worker_id="worker-restarted")
        chunks = pipeline.chunks(document)
        assert pipeline.version(document).status is ProcessingStatus.READY
        assert "partial" not in {chunk.text for chunk in chunks}
        assert pipeline.version(document).chunk_count == len(chunks)

    async def test_a_document_that_kills_every_worker_eventually_fails(self) -> None:
        pipeline = await build_pipeline(max_attempts=2)
        pipeline.embedder.failures.extend(WorkerKilledError() for _ in range(5))
        document = await pipeline.upload("report.pdf", _pdf())
        for _ in range(2):
            with pytest.raises(WorkerKilledError):
                await pipeline.drain()
            pipeline.clock.advance(LEASE + timedelta(seconds=1))
            await pipeline.recovery.execute()

        version = pipeline.version(document)
        assert version.status is ProcessingStatus.FAILED
        assert version.failure_code == FailureCode.INTERRUPTED
        assert pipeline.queue.dispatched == []

    async def test_a_presumed_dead_worker_that_wakes_up_cannot_overwrite_its_successor(
        self,
    ) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload("report.pdf", _pdf())
        (job_id,) = pipeline.dispatched_job_ids()
        successor_results = []

        async def stall_past_lease_while_another_worker_finishes() -> None:
            pipeline.clock.advance(LEASE + timedelta(seconds=1))
            await pipeline.recovery.execute()
            successor_results.extend(await pipeline.drain(worker_id="worker-b"))

        pipeline.embedder.before_return = stall_past_lease_while_another_worker_finishes
        zombie = await pipeline.run(job_id, worker_id="worker-a")

        assert [r.outcome for r in successor_results] == [JobOutcome.SUCCEEDED]
        assert zombie.outcome is JobOutcome.LEASE_LOST
        version = pipeline.version(document)
        assert version.status is ProcessingStatus.READY
        first, second = pipeline.jobs(document)
        assert first.error_code == FailureCode.WORKER_LOST, (
            "the zombie did not overwrite its record"
        )
        assert second.status is JobStatus.SUCCEEDED


# ---------------------------------------------------------------------------
# Superseded work
# ---------------------------------------------------------------------------


class TestSupersession:
    async def test_a_version_replaced_before_processing_is_closed_out(self) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload("a.txt", (PROSE * 3).encode())
        old_version = pipeline.version(document)

        async def newer() -> AsyncIterator[bytes]:
            yield b"a newer revision of the text. " * 20

        await pipeline.add_version.execute(
            pipeline.ctx, document.id, filename="a.txt", content_stream=newer()
        )
        results = await pipeline.drain()

        assert sorted(r.outcome.value for r in results) == ["failed", "succeeded"]
        old = pipeline.uow_factory.state.versions[old_version.id]
        assert (old.status, old.failure_code) == (
            ProcessingStatus.FAILED,
            "DOCUMENT_VERSION_SUPERSEDED",
        )
        assert pipeline.version(document).status is ProcessingStatus.READY
        assert old_version.id not in pipeline.uow_factory.state.chunks

    async def test_a_version_replaced_mid_run_is_not_indexed(self) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload("a.txt", (PROSE * 3).encode())
        old_version = pipeline.version(document)
        (job_id,) = pipeline.dispatched_job_ids()

        async def upload_newer_version() -> None:
            async def newer() -> AsyncIterator[bytes]:
                yield b"replacement content arrives mid-run. " * 20

            await pipeline.add_version.execute(
                pipeline.ctx, document.id, filename="a.txt", content_stream=newer()
            )

        pipeline.embedder.before_return = upload_newer_version
        result = await pipeline.run(job_id)

        assert (result.outcome, result.reason) == (JobOutcome.FAILED, "DOCUMENT_VERSION_SUPERSEDED")
        assert old_version.id not in pipeline.uow_factory.state.chunks

    async def test_a_deleted_document_is_not_processed(self) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload("a.txt", PROSE.encode())
        state = pipeline.uow_factory.state
        state.documents[document.id] = dataclasses.replace(
            state.documents[document.id], deleted_at=pipeline.clock.now()
        )
        results = await pipeline.drain()
        assert results[0].reason == "DOCUMENT_VERSION_SUPERSEDED"
        assert pipeline.embedder.calls == 0


# ---------------------------------------------------------------------------
# Lost messages, reprocessing, status
# ---------------------------------------------------------------------------


class TestLostMessages:
    async def test_recovery_republishes_a_queued_job_whose_message_was_lost(self) -> None:
        pipeline = await build_pipeline()
        pipeline.queue.fail = True
        document = await pipeline.upload("a.txt", PROSE.encode())
        pipeline.queue.fail = False

        assert (await pipeline.recovery.execute()).redelivered == 0, "not before the grace period"
        pipeline.clock.advance(timedelta(minutes=11))
        assert (await pipeline.recovery.execute()).redelivered == 1
        assert (await pipeline.recovery.execute()).redelivered == 0, (
            "not again within the grace period"
        )

        await pipeline.drain()
        assert pipeline.version(document).status is ProcessingStatus.READY


class TestReprocess:
    async def test_a_failed_document_can_be_reprocessed_with_a_fresh_budget(self) -> None:
        pipeline = await build_pipeline(max_attempts=2)
        pipeline.embedder.failures.extend(AIProviderUnavailableError("503") for _ in range(2))
        document = await pipeline.upload("a.txt", (PROSE * 3).encode())
        await pipeline.drain()
        assert pipeline.version(document).failure_code == FailureCode.RETRIES_EXHAUSTED

        refreshed = await pipeline.reprocess.execute(pipeline.ctx, document.id)

        assert refreshed.current_version is not None
        assert refreshed.current_version.status is ProcessingStatus.PENDING
        assert refreshed.current_version.failure_code is None
        newest = pipeline.jobs(document)[-1]
        assert (newest.attempt, newest.run_attempt) == (3, 1)
        await pipeline.drain()
        assert pipeline.version(document).status is ProcessingStatus.READY

    async def test_only_failed_documents_can_be_reprocessed(self) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload("a.txt", PROSE.encode())
        with pytest.raises(ConflictError):
            await pipeline.reprocess.execute(pipeline.ctx, document.id)

    async def test_reprocessing_requires_update_permission(self) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload("a.txt", PROSE.encode())
        viewer = AccessContext(
            user_id=pipeline.ctx.user_id, workspace_id=pipeline.ctx.workspace_id, role=Role.VIEWER
        )
        with pytest.raises(PermissionDeniedError):
            await pipeline.reprocess.execute(viewer, document.id)


class TestStatus:
    async def test_status_lists_attempts_newest_first(self) -> None:
        pipeline = await build_pipeline()
        pipeline.embedder.failures.append(AIProviderUnavailableError("503"))
        document = await pipeline.upload("a.txt", (PROSE * 3).encode())
        await pipeline.drain()

        view = await pipeline.status.execute(pipeline.ctx, document.id)

        assert view.version.status is ProcessingStatus.READY
        assert [job.attempt for job in view.jobs] == [2, 1]
        assert [job.status for job in view.jobs] == [JobStatus.SUCCEEDED, JobStatus.FAILED]

    async def test_another_tenants_document_status_is_not_found(self) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload("a.txt", PROSE.encode())
        stranger = AccessContext(
            user_id=pipeline.ctx.user_id, workspace_id=uuid.uuid4(), role=Role.OWNER
        )
        with pytest.raises(NotFoundError):
            await pipeline.status.execute(stranger, document.id)
