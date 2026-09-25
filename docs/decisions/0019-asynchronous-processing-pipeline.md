# 0019 — Durable jobs with leases and fencing; the database, not the broker, owns work

- **Status:** Accepted — amends 0002 (worker database access)
- **Date:** 2026-09-17

## Context

ADR-0002 chose Celery with a prefork pool so that a hostile document can kill
one child process and nothing else. It left three questions open that decide
whether the pipeline is actually correct rather than merely fast:

1. **Where does the truth about pending work live?** Redis is explicitly
   disposable (docs/architecture/system.md). A publish can fail after the
   upload commits; a broker restart can drop a message; `acks_late` delivers
   at least once, so a message can also arrive twice.
2. **What happens when a worker dies mid-job?** Prefork plus a hard time limit
   means SIGKILL is a *normal* event. With a Redis broker, an unacknowledged
   message returns only after the visibility timeout (16 minutes), and a job
   whose worker died has no one to record its outcome.
3. **What stops a worker that was presumed dead from finishing anyway?** A
   worker paused by a long GC, a network partition, or a slow stage can outlive
   the moment everyone else decided it was gone.

A pipeline that answers these with "Celery retries" corrupts state in exactly
the situations it exists to survive: duplicate chunk sets, documents stuck in
`PROCESSING` forever, or a retried attempt's result overwritten by the original.

## Decision

### The job row is the source of truth; the message is a doorbell

`document_processing_jobs` holds one row **per attempt**. The upload inserts the
version (`PENDING`) and its first job (`QUEUED`) **in one transaction**, and
publishes `{job_id, request_id}` only after commit. A version therefore never
exists without the job that will process it.

If the publish fails, the upload still succeeds — the job exists and will run.
A **recovery sweep** re-publishes any `QUEUED` job whose last publish is older
than a grace period (`enqueued_at`), so a lost message costs latency, never the
document. A duplicate message costs nothing: claiming is conditional.

### Claiming is a locked decision; running is a lease

A worker claims a job with `SELECT … FOR UPDATE SKIP LOCKED` on the job and its
version, and a **pure** function decides what to do with the snapshot:

| Snapshot | Decision |
|---|---|
| job `SUCCEEDED`/`FAILED` | no-op — a redelivered message |
| job `RUNNING`, lease live | no-op — held elsewhere |
| job `RUNNING`, lease expired | **abandoned**: record `WORKER_LOST`, schedule the next attempt |
| job `QUEUED`, not yet due | no-op — early message; recovery re-publishes later |
| version not current / document deleted | fail as superseded, no processing |
| job `QUEUED`, version already terminal | fail as `JOB_STATE_INCONSISTENT`, version untouched |
| otherwise | `RUNNING` with `worker_id` + `lease_expires_at` |

`worker_id` is unique **per execution** (host/pid/ULID), not per process, so a
redelivered message handled by the same process never inherits a previous
attempt's lease. The lease is renewed at each stage boundary, and defaults to
longer than Celery's hard time limit: an expired lease is proof the holder is
gone, because the pool would have killed it by then.

### Every worker write is fenced

Each state change a worker makes is one conditional statement:

```sql
UPDATE document_processing_jobs SET …
 WHERE id = :job AND status = 'running' AND worker_id = :me
```

and it reports whether a row matched. The index transaction runs that fenced
completion **first**, then locks the version, replaces the chunks, and marks the
version `READY` — all in one transaction. A zombie whose lease was taken over
matches no row, raises `LeaseLostError`, and its transaction rolls back with
every chunk it inserted. Lock order is always job → version, in both the claim
and the index transaction, so the two cannot deadlock.

### Retries are rows, not broker countdowns

A transient failure ends its job `FAILED` (`failure_kind = transient`), inserts
the next attempt `QUEUED` with `scheduled_for = now + backoff`, and returns the
version to `PENDING`. The message is published with a matching countdown, but
the **backoff is durable in the database**: a Redis restart loses the countdown,
not the schedule.

- Backoff: exponential, capped, **equal jitter** (half fixed, half random), so a
  provider outage does not bring every document back at the same instant.
- Budget: `run_attempt` counts attempts within one processing run. Exhausting it
  fails the version with `PROCESSING_RETRIES_EXHAUSTED` (or
  `PROCESSING_INTERRUPTED` for repeated worker loss) and a message that does not
  blame the file. An explicit reprocess starts a new run with a fresh budget.
- Permanent failures and defects never retry.

`uq_jobs_active_per_version` (a partial unique index over `queued`/`running`)
makes two live jobs for one version unrepresentable; check constraints make
"running without a lease" and "failed without a classification" unrepresentable.

### Failure classification is a port

The domain classifies what ORBIT raises on purpose (`DocumentProcessingError` →
permanent, `retryable` → transient, anything else → defect). An infrastructure
`FailureClassifier` handles what only infrastructure may import: SQLAlchemy and
asyncpg connection errors and deadlocks, botocore and httpx transport errors,
Celery's soft time limit. **Unknown exceptions are defects** — guessing
"transient" retries a bug five times; guessing "permanent" blames the user.

### The worker runs the async stack, one event loop per task

ADR-0002 anticipated a second, synchronous SQLAlchemy stack for the worker. That
is **not** built. The worker reuses the async ports (unit of work, repositories,
object storage) by running each task as `asyncio.run(...)` with a container
built for that task and disposed after it.

The reason ADR-0002 gave for synchronous sessions — prefork, blocking parsers —
is unaffected: parsing is still synchronous CPU work inside a forked child,
still under SIGKILL limits. What a second stack would have cost is a duplicate
of every repository, kept in sync forever. The price paid instead is small and
explicit: asyncpg connections are loop-bound, so nothing loop-bound survives
between tasks (a fresh engine per task — negligible next to a document that
takes seconds to minutes).

### Recovery runs on worker start and on a schedule

`RecoverStalledJobs` (abandoned leases + undelivered queued jobs) runs:

- in `worker_ready`, so **restarting a crashed worker is itself the recovery**;
- every `ORBIT_PROCESSING_RECOVERY_INTERVAL_SECONDS` via `celery beat`;
- on demand: `python -m orbit.composition.worker recover`.

It is safe to run concurrently with itself and with live workers, because every
step is a row-locked conditional transition.

## Alternatives considered

**Celery `autoretry_for` / `self.retry` for all retries.** Least code. Rejected:
the retry schedule would live only in the broker, invisible to the API and lost
with Redis; attempts would not be rows an operator can read; and it offers
nothing for a worker that died holding the job. It *is* used for one narrow case —
when the outcome itself cannot be recorded because the database is down, the
message is redelivered with backoff, since no state changed.

**Rely on `acks_late` redelivery for crash recovery.** Rejected: redelivery is
tied to the Redis visibility timeout (16 minutes), happens even for jobs that
completed but whose ack was lost, and does nothing to stop the original worker
if it was not actually dead. Leases plus fencing handle all three.

**Heartbeat thread renewing the lease continuously.** Tighter leases. Rejected
for now: a thread inside a prefork child adds a second failure mode (the
heartbeat outliving a wedged parse), and stage-boundary renewal with a lease
longer than the hard limit is already correct. Fencing makes a too-short lease
wasteful, never unsafe.

**A synchronous SQLAlchemy stack for the worker** (ADR-0002's plan). Rejected as
above: two implementations of every repository to buy nothing the async stack
lacks under one-task-per-process.

**A Postgres queue (`SKIP LOCKED` polling) instead of Celery.** Now closer than
ADR-0002 judged, because the job table already has scheduling and leases.
Still rejected: Celery provides the prefork isolation and time limits that are
the reason for ADR-0002, and polling would add latency or a LISTEN/NOTIFY layer.
The durable job table means migrating later changes the doorbell, not the model.

## Consequences

- Nothing depends on Redis keeping a message. The observable cost of a Redis
  loss is latency bounded by the redelivery grace period.
- A worker restart never corrupts state; this is exercised by an integration test
  that hard-kills a real worker process mid-job and verifies a restarted worker
  brings the document to `READY` with exactly one chunk set.
- Every attempt's outcome, stage, classification, and operator detail is
  queryable in SQL. The API exposes the user-safe subset at
  `GET /documents/{id}/processing`.
- `beat` is a separate process to run in production. Without it, recovery still
  happens on every worker start, but a lost message on a long-lived worker waits
  for the next restart.
- The lease must exceed the hard time limit in production (enforced by
  `Settings`). Shortening it for tests or demos is safe but may duplicate work.
- Prometheus metrics for queue depth and stage durations remain M8 work
  (ADR-0015); every stage already emits a structured completion record with its
  duration, which is where those metrics will be derived from.
