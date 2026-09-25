"""The pure rules: claim decisions, retry schedule, and failure classification."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import botocore.exceptions
import httpx
import pytest
import sqlalchemy.exc
from celery.exceptions import SoftTimeLimitExceeded

from orbit.domain.errors import (
    AIProviderUnavailableError,
    ConflictError,
    DocumentCorruptError,
    StorageUnavailableError,
)
from orbit.domain.models.entities import DocumentVersion, ProcessingStatus
from orbit.domain.processing.failures import (
    MAX_FAILURE_DETAIL_LENGTH,
    FailureCode,
    FailureKind,
    ProcessingFailure,
    classify_orbit_error,
)
from orbit.domain.processing.jobs import (
    ClaimDecision,
    ClaimSnapshot,
    JobStatus,
    ProcessingJob,
    decide_claim,
)
from orbit.domain.processing.retry import RetryPolicy
from orbit.infrastructure.processing.failure_classifier import PipelineFailureClassifier

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def _constant(value: float) -> Callable[[], float]:
    return lambda: value


TOLERANCE = timedelta(seconds=30)


def _version(**overrides: object) -> DocumentVersion:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "document_id": uuid.uuid4(),
        "workspace_id": uuid.uuid4(),
        "version_number": 1,
        "is_current": True,
        "storage_key": "k",
        "content_sha256": "0" * 64,
        "byte_size": 1,
        "content_type": "text/plain",
        "original_filename": "a.txt",
        "status": ProcessingStatus.PENDING,
        "chunk_count": 0,
        "created_at": NOW,
    }
    values.update(overrides)
    return DocumentVersion(**values)  # type: ignore[arg-type]


def _job(**overrides: object) -> ProcessingJob:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "workspace_id": uuid.uuid4(),
        "document_version_id": uuid.uuid4(),
        "status": JobStatus.QUEUED,
        "attempt": 1,
        "run_attempt": 1,
        "scheduled_for": NOW,
        "created_at": NOW,
    }
    values.update(overrides)
    return ProcessingJob(**values)  # type: ignore[arg-type]


def _decide(
    job: ProcessingJob, version: DocumentVersion | None = None, *, deleted: bool = False
) -> ClaimDecision:
    snapshot = ClaimSnapshot(job=job, version=version or _version(), document_deleted=deleted)
    return decide_claim(snapshot, now=NOW, early_tolerance=TOLERANCE)


class TestClaimDecision:
    def test_a_due_queued_job_proceeds(self) -> None:
        assert _decide(_job()) is ClaimDecision.PROCEED

    @pytest.mark.parametrize("status", [JobStatus.SUCCEEDED, JobStatus.FAILED])
    def test_a_redelivered_message_for_a_finished_job_is_a_no_op(self, status: JobStatus) -> None:
        assert _decide(_job(status=status)) is ClaimDecision.ALREADY_FINISHED

    def test_finished_wins_even_if_the_document_was_since_deleted(self) -> None:
        assert (
            _decide(_job(status=JobStatus.SUCCEEDED), deleted=True)
            is ClaimDecision.ALREADY_FINISHED
        )

    def test_a_running_job_with_a_live_lease_is_held_elsewhere(self) -> None:
        job = _job(
            status=JobStatus.RUNNING, worker_id="w", lease_expires_at=NOW + timedelta(seconds=1)
        )
        assert _decide(job) is ClaimDecision.HELD_ELSEWHERE

    def test_a_running_job_whose_lease_expired_was_abandoned(self) -> None:
        job = _job(
            status=JobStatus.RUNNING, worker_id="w", lease_expires_at=NOW - timedelta(seconds=1)
        )
        assert _decide(job) is ClaimDecision.ABANDONED

    def test_a_backoff_that_has_not_elapsed_is_not_due(self) -> None:
        assert _decide(_job(scheduled_for=NOW + timedelta(minutes=5))) is ClaimDecision.NOT_DUE

    def test_a_slightly_early_message_is_honoured(self) -> None:
        assert _decide(_job(scheduled_for=NOW + timedelta(seconds=10))) is ClaimDecision.PROCEED

    def test_a_superseded_version_or_deleted_document_is_not_processed(self) -> None:
        assert _decide(_job(), _version(is_current=False)) is ClaimDecision.SUPERSEDED
        assert _decide(_job(), deleted=True) is ClaimDecision.SUPERSEDED

    @pytest.mark.parametrize("status", [ProcessingStatus.READY, ProcessingStatus.FAILED])
    def test_a_queued_job_for_a_terminal_version_is_inconsistent(
        self, status: ProcessingStatus
    ) -> None:
        assert _decide(_job(), _version(status=status)) is ClaimDecision.INCONSISTENT


class TestRetryPolicy:
    TRANSIENT = ProcessingFailure.transient(code="X", detail="d")
    PERMANENT = ProcessingFailure.permanent(DocumentCorruptError("bad"))
    DEFECT = ProcessingFailure.defect(detail="bug")

    def test_only_transient_failures_retry(self) -> None:
        policy = RetryPolicy(max_attempts=5)
        assert policy.should_retry(self.TRANSIENT, run_attempt=1)
        assert not policy.should_retry(self.PERMANENT, run_attempt=1)
        assert not policy.should_retry(self.DEFECT, run_attempt=1)

    def test_retries_stop_at_the_attempt_limit(self) -> None:
        policy = RetryPolicy(max_attempts=3)
        assert policy.should_retry(self.TRANSIENT, run_attempt=2)
        assert not policy.should_retry(self.TRANSIENT, run_attempt=3)

    def test_delay_grows_exponentially_within_equal_jitter_bounds(self) -> None:
        policy = RetryPolicy(base_delay=timedelta(seconds=10), max_delay=timedelta(hours=1))
        for attempt, ceiling in [(2, 10), (3, 20), (4, 40), (5, 80)]:
            low = policy.delay_before(attempt, random=lambda: 0.0).total_seconds()
            high = policy.delay_before(attempt, random=lambda: 0.999999).total_seconds()
            assert low == pytest.approx(ceiling / 2)
            assert high == pytest.approx(ceiling, rel=1e-3)

    def test_delay_is_capped(self) -> None:
        policy = RetryPolicy(base_delay=timedelta(seconds=10), max_delay=timedelta(seconds=60))
        assert policy.delay_before(40, random=lambda: 0.999999) <= timedelta(seconds=60)

    def test_jitter_spreads_simultaneous_failures(self) -> None:
        policy = RetryPolicy()
        delays = {policy.delay_before(3, random=_constant(v / 10)) for v in range(10)}
        assert len(delays) == 10

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_attempts": 0},
            {"base_delay": timedelta(0)},
            {"base_delay": timedelta(seconds=10), "max_delay": timedelta(seconds=5)},
        ],
    )
    def test_incoherent_policies_are_rejected(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            RetryPolicy(**kwargs)  # type: ignore[arg-type]

    def test_attempt_one_is_never_a_retry(self) -> None:
        with pytest.raises(ValueError, match="at least 2"):
            RetryPolicy().delay_before(1, random=lambda: 0.5)


class TestFailures:
    def test_exhausting_a_transient_failure_does_not_blame_the_file(self) -> None:
        exhausted = ProcessingFailure.transient(
            code="AI_PROVIDER_UNAVAILABLE", detail="503"
        ).exhausted()
        assert exhausted.code == FailureCode.RETRIES_EXHAUSTED
        assert "Nothing is wrong with the file" in exhausted.user_message

    def test_exhausting_worker_loss_reports_interruption(self) -> None:
        exhausted = ProcessingFailure.worker_lost(detail="lease expired").exhausted()
        assert exhausted.code == FailureCode.INTERRUPTED

    def test_defects_get_a_generic_message_and_keep_detail_for_operators(self) -> None:
        failure = ProcessingFailure.defect(detail="KeyError: 'secret_column'")
        assert "secret_column" not in failure.user_message
        assert "secret_column" in failure.detail

    def test_operator_detail_is_bounded(self) -> None:
        failure = ProcessingFailure.defect(detail="x" * 10_000)
        assert len(failure.bounded_detail()) == MAX_FAILURE_DETAIL_LENGTH

    def test_domain_errors_classify_by_their_taxonomy(self) -> None:
        assert classify_orbit_error(DocumentCorruptError("bad")).kind is FailureKind.PERMANENT
        assert classify_orbit_error(StorageUnavailableError("down")).kind is FailureKind.TRANSIENT
        # A conflict inside the pipeline is ORBIT's bug, not the document's.
        assert classify_orbit_error(ConflictError("race")).kind is FailureKind.DEFECT

    def test_a_permanent_failure_carries_the_errors_user_message(self) -> None:
        failure = classify_orbit_error(DocumentCorruptError("This PDF appears to be damaged."))
        assert failure.user_message == "This PDF appears to be damaged."
        assert failure.code == "DOCUMENT_CORRUPT"


class TestInfrastructureClassifier:
    classifier = PipelineFailureClassifier()

    @pytest.mark.parametrize(
        ("exc", "code"),
        [
            (
                sqlalchemy.exc.OperationalError("SELECT 1", {}, Exception("conn")),
                "DATABASE_UNAVAILABLE",
            ),
            (
                sqlalchemy.exc.InterfaceError("SELECT 1", {}, Exception("closed")),
                "DATABASE_UNAVAILABLE",
            ),
            (sqlalchemy.exc.TimeoutError("pool exhausted"), "DATABASE_UNAVAILABLE"),
            (
                botocore.exceptions.EndpointConnectionError(endpoint_url="http://s3"),
                "STORAGE_UNAVAILABLE",
            ),
            (httpx.ConnectTimeout("slow"), "AI_PROVIDER_UNAVAILABLE"),
            (ConnectionResetError("reset"), "DEPENDENCY_UNAVAILABLE"),
            (AIProviderUnavailableError("429"), "AI_PROVIDER_UNAVAILABLE"),
        ],
    )
    def test_dependency_failures_are_transient(self, exc: BaseException, code: str) -> None:
        failure = self.classifier.classify(exc)
        assert failure.kind is FailureKind.TRANSIENT
        assert failure.code == code

    def test_a_deadlock_is_transient(self) -> None:
        class OrigError(Exception):
            sqlstate = "40P01"

        exc = sqlalchemy.exc.DBAPIError("UPDATE", {}, OrigError("deadlock"))
        assert self.classifier.classify(exc).kind is FailureKind.TRANSIENT

    def test_an_integrity_error_is_a_defect(self) -> None:
        exc = sqlalchemy.exc.IntegrityError("INSERT", {}, Exception("dup"))
        assert self.classifier.classify(exc).kind is FailureKind.DEFECT

    def test_the_soft_time_limit_is_a_permanent_timeout(self) -> None:
        failure = self.classifier.classify(SoftTimeLimitExceeded())
        assert failure.kind is FailureKind.PERMANENT
        assert failure.code == "DOCUMENT_PROCESSING_TIMEOUT"

    def test_memory_exhaustion_is_a_permanent_limit(self) -> None:
        assert self.classifier.classify(MemoryError()).code == "DOCUMENT_LIMIT_EXCEEDED"

    def test_anything_unrecognised_is_a_defect_never_retried(self) -> None:
        failure = self.classifier.classify(KeyError("chunk_count"))
        assert failure.kind is FailureKind.DEFECT
        assert failure.code == FailureCode.INTERNAL
        assert "chunk_count" in failure.detail
        assert "chunk_count" not in failure.user_message
