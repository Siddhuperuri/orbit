"""Processing jobs and the state machine they drive.

Two state machines run side by side, and keeping them distinct is what makes
both of them simple:

**The version** -- what a user sees:

```
PENDING ──claim──▶ PROCESSING ──index──▶ READY
   ▲                   │
   └──transient retry──┤
                       └──permanent / defect / exhausted──▶ FAILED
```

**The job** -- one row per attempt, what an operator sees:

```
QUEUED ──claim──▶ RUNNING ──▶ SUCCEEDED
                     │
                     └──────▶ FAILED (failure_kind, error_code)
```

A transient failure ends its job as `FAILED` and inserts the *next* attempt as
`QUEUED`, so "what happened on each try" is answerable from the table rather
than from a counter that was overwritten.

**Leases** are what make a worker restart safe. A `RUNNING` job carries the id
of the worker execution that holds it and an expiry. A worker that dies simply
stops renewing; once the lease has expired, recovery records that attempt as
`WORKER_LOST` and schedules the next. A worker that was only *presumed* dead and
comes back finds its writes fenced: every write it makes is conditional on still
holding the lease, so it cannot overwrite its successor's result.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from orbit.domain.models.entities import DocumentVersion, ProcessingStatus
from orbit.domain.processing.failures import FailureKind


class JobStatus(StrEnum):
    """Mirrors the database enum."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED)


class PipelineStage(StrEnum):
    """The last stage a job reached. Recorded on the row as each begins, so a
    job abandoned by a dead worker still says where it died."""

    CLAIMED = "claimed"
    FETCH = "fetch"
    PARSE = "parse"
    NORMALIZE = "normalize"
    CHUNK = "chunk"
    EMBED = "embed"
    INDEX = "index"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class ProcessingJob:
    id: uuid.UUID
    workspace_id: uuid.UUID
    document_version_id: uuid.UUID
    status: JobStatus
    #: Unique per version across its whole history.
    attempt: int
    #: 1-based position within the current processing run. The retry budget is
    #: measured against this, so an explicit reprocess starts a fresh budget.
    run_attempt: int
    #: Not before this instant. Backoff is expressed here, in the database,
    #: rather than only as a broker countdown that a Redis restart would lose.
    scheduled_for: datetime
    created_at: datetime
    enqueued_at: datetime | None = None
    request_id: str | None = None
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    stage: PipelineStage | None = None
    error_code: str | None = None
    failure_kind: FailureKind | None = None
    #: Operator detail. Never shown to users.
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    """The queue as PostgreSQL sees it -- the source of truth, not the broker.

    Exported as gauges, so an operator can tell "nothing is wrong" from "a
    dead worker's jobs are waiting for recovery" without a database console.
    """

    #: QUEUED and due: waiting for a worker.
    ready: int
    #: QUEUED for a later time: waiting out a retry backoff.
    scheduled: int
    #: RUNNING with a live lease.
    running: int
    #: RUNNING whose lease has expired: abandoned, awaiting recovery.
    lease_expired: int
    #: How long the oldest ready job has been due; None when nothing is ready.
    oldest_ready_age_seconds: float | None


@dataclass(frozen=True, slots=True)
class ClaimSnapshot:
    """Everything a claim decision needs, read under a row lock."""

    job: ProcessingJob
    version: DocumentVersion
    document_deleted: bool


class ClaimDecision(StrEnum):
    #: Queued, due, and the version is still worth processing.
    PROCEED = "proceed"
    #: A redelivered message for a job that already finished. The normal,
    #: expected consequence of at-least-once delivery -- not an error.
    ALREADY_FINISHED = "already_finished"
    #: Another live execution holds the lease.
    HELD_ELSEWHERE = "held_elsewhere"
    #: The holder's lease expired without an outcome: the worker died.
    ABANDONED = "abandoned"
    #: A backoff delay has not elapsed; the message arrived early.
    NOT_DUE = "not_due"
    #: The version was replaced by a newer upload, or its document deleted.
    SUPERSEDED = "superseded"
    #: A queued job whose version is already terminal. No correct code path
    #: produces this; it is recorded loudly rather than processed.
    INCONSISTENT = "inconsistent"


_PROCESSABLE_VERSION_STATES = frozenset({ProcessingStatus.PENDING, ProcessingStatus.PROCESSING})


def decide_claim(  # noqa: PLR0911 -- one return per decision, in priority order
    snapshot: ClaimSnapshot, *, now: datetime, early_tolerance: timedelta
) -> ClaimDecision:
    """Decide what a worker holding a message for this job should do.

    Pure: the decision is made from a snapshot read under lock, and applying it
    is the caller's job. Order matters -- a finished job is finished regardless
    of what has happened to its document since.
    """
    job = snapshot.job
    if job.status.is_terminal:
        return ClaimDecision.ALREADY_FINISHED

    if job.status is JobStatus.RUNNING:
        if job.lease_expires_at is not None and job.lease_expires_at > now:
            return ClaimDecision.HELD_ELSEWHERE
        return ClaimDecision.ABANDONED

    # QUEUED from here on.
    if snapshot.document_deleted or not snapshot.version.is_current:
        return ClaimDecision.SUPERSEDED
    if snapshot.version.status not in _PROCESSABLE_VERSION_STATES:
        return ClaimDecision.INCONSISTENT
    if job.scheduled_for > now + early_tolerance:
        return ClaimDecision.NOT_DUE
    return ClaimDecision.PROCEED
