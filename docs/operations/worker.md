# Worker operations

The document processing worker: how to run it, what its limits are, how to tell
whether it is healthy, and what to do when a document is stuck. Design:
[ADR-0002](../decisions/0002-celery-prefork-job-queue.md),
[ADR-0019](../decisions/0019-asynchronous-processing-pipeline.md),
[data-flow §2](../architecture/data-flow.md#2-document-processing).

## Processes

| Process | Command | Required |
|---|---|---|
| Worker | `celery --app orbit.composition.worker:celery_app worker --pool=prefork --concurrency=N` | Yes |
| Scheduler | `celery --app orbit.composition.worker:celery_app beat` | Yes in production (see below) |
| One-off recovery | `python -m orbit.composition.worker recover` | Manual |
| Embedding coverage report | `python -m orbit.composition.worker index-status` (exit 3 while stale chunks remain) | After an embedding model change |
| In-place re-embedding | `python -m orbit.composition.worker reindex-embeddings [--max-chunks N]` | After an embedding model change; see [embeddings.md](../database/embeddings.md) |

Locally: `npm run dev:worker` and `npm run dev:beat`. On Windows the worker uses
`--pool=solo`, because prefork cannot fork there; **time limits are not enforced
under `solo`**, so a hostile document can run unbounded in local development.
Compose runs `worker` (prefork) and `scheduler` (beat) in Linux containers.

Run exactly **one** beat process per deployment. Two schedulers double the
recovery sweeps; that is safe (sweeps are row-locked) but wasteful.

Without beat, recovery still runs every time a worker starts. A lost message on a
long-running worker would then wait for the next restart.

## Limits

| Setting | Default | Effect |
|---|---|---|
| `ORBIT_PROCESSING_TASK_TIME_LIMIT_SECONDS` | 900 | Child SIGKILLed. Recorded later as `WORKER_LOST` by recovery. |
| `ORBIT_PROCESSING_TASK_SOFT_TIME_LIMIT_SECONDS` | 840 | Raised in the task; recorded immediately as `DOCUMENT_PROCESSING_TIMEOUT` (permanent). |
| `ORBIT_PROCESSING_LEASE_SECONDS` | 960 | A `RUNNING` job untouched this long is presumed abandoned. **Must exceed the hard limit in production** (enforced). |
| `ORBIT_WORKER_MAX_MEMORY_PER_CHILD_KB` | 512000 | Child retired after the task that crossed it. |
| `ORBIT_PROCESSING_MAX_PAGES` / `_MAX_CHARACTERS` / `_MAX_CHUNKS` | 2000 / 5M / 20000 | `DOCUMENT_LIMIT_EXCEEDED`, permanent. |
| `worker_prefetch_multiplier` | 1 | One reserved task per child. |
| `task_acks_late` + `task_reject_on_worker_lost` | on | A killed child's message is redelivered. |

## Retries

| Setting | Default |
|---|---|
| `ORBIT_PROCESSING_MAX_ATTEMPTS` | 5 attempts per processing run |
| `ORBIT_PROCESSING_RETRY_BASE_SECONDS` | 15 |
| `ORBIT_PROCESSING_RETRY_MAX_SECONDS` | 900 |

Delay before attempt *n* is `min(max, base · 2^(n-2))`, half fixed and half
random. Only transient failures (dependency outages, worker loss) retry.

A separate, narrower retry exists for when the worker cannot even **record** an
outcome (database unreachable): the Celery message is redelivered up to 8 times,
5 s doubling to 300 s. No job state has changed in that case, so redelivery is
exact. Past that, the job remains `QUEUED`/`RUNNING` in the database and
recovery picks it up.

## Health signals

All worker records are JSON with `service=orbit-worker`, and every record inside
a task carries `job_id`, `request_id` (from the upload), `document_id`, and
`workspace_id`.

| Event | Meaning | Alert? |
|---|---|---|
| `pipeline.stage_completed` | a stage finished; `stage`, `duration_ms`, and its output counts | Dashboard |
| `pipeline.completed` | `outcome`, `total_ms`, `stages_ms` | Dashboard |
| `job.retry_scheduled` | transient failure; `error_code`, `delay_seconds` | Rate |
| `job.failed_permanently` | terminal; `version_failure_code`, `failure_kind` | Rate by code |
| `pipeline.attempt_failed` at **error** level | a **defect** (with traceback) | **Page** |
| `job.abandoned` | a worker died holding a job | Rate |
| `job.lease_lost` | a slow worker was superseded; its work was discarded | Investigate if sustained |
| `job.redelivered` | a message was lost and re-published | Investigate if sustained |
| `job.inconsistent_state` | a queued job for a terminal version | **Page** — a bug |
| `task.outcome_not_recorded` | database unreachable from the worker | **Page** |
| `processing.enqueue_failed` | publish failed after commit (API or worker) | Rate |
| `worker.startup_recovery_failed` | worker started without database access | **Page** |
| `embedding.retrying` | a provider request is being retried in-process; `status`, `delay_seconds` | Rate |
| `embedding.retries_exhausted` / `embedding.retry_deferred` | the provider stayed down, or asked to wait longer than the in-process cap; the job's backoff takes over | Rate — sustained means a provider outage |
| `pipeline.attempt_failed` (defect) whose `detail` names `ConfigurationError` | credentials, quota, model, or embedding width misconfigured; affected documents need a reprocess once fixed | **Page** |
| `readiness.embedding_schema_mismatch` | `ORBIT_EMBEDDING_DIMENSIONS` disagrees with the vector column | **Page** |
| `embedding.reindex_inconsistent_chunk` | a chunk's text disagrees with its recorded input hash | **Page** — a bug |

Queue depth and age are answerable from the database without a broker query:

```sql
-- Backlog and the oldest due job
SELECT count(*), min(scheduled_for) FROM document_processing_jobs
 WHERE status = 'queued' AND scheduled_for <= now();

-- Running jobs and their leases
SELECT id, worker_id, stage, started_at, lease_expires_at
  FROM document_processing_jobs WHERE status = 'running' ORDER BY started_at;

-- Failures in the last day, by cause
SELECT error_code, failure_kind, count(*) FROM document_processing_jobs
 WHERE status = 'failed' AND finished_at > now() - interval '1 day'
 GROUP BY 1, 2 ORDER BY 3 DESC;
```

The same facts are exported as Prometheus gauges and histograms
(`orbit_queue_jobs`, `orbit_job_queue_wait_seconds`, ...); see
[observability.md](observability.md), and [runbook.md](runbook.md) for the
investigation procedure.

## Runbook

**"My document has been pending for a long time."**

```sql
SELECT j.attempt, j.run_attempt, j.status, j.stage, j.scheduled_for, j.enqueued_at,
       j.lease_expires_at, j.error_code, j.failure_kind, j.error_message, j.request_id
  FROM document_processing_jobs j
  JOIN document_versions v ON v.id = j.document_version_id
 WHERE v.document_id = :document_id AND v.is_current
 ORDER BY j.attempt;
```

- `queued`, `scheduled_for` in the future → waiting out a backoff. Look at the
  previous attempt's `error_code`.
- `queued`, due, `enqueued_at` old → message lost; the next sweep re-publishes it.
  Force it with `python -m orbit.composition.worker recover`.
- `running`, `lease_expires_at` in the past → the worker died; the next sweep or
  worker start recovers it.
- `running`, lease in the future → a worker is on it. Search logs by `job_id`.

**"Every document is failing."** Group recent failures by `error_code`.
`AI_PROVIDER_UNAVAILABLE` / `DATABASE_UNAVAILABLE` / `STORAGE_UNAVAILABLE` mean
a dependency; documents stay `PENDING` until their budget runs out.
`INTERNAL_PROCESSING_ERROR` means a defect — the traceback is in the
`pipeline.attempt_failed` error record for that `job_id`. An invalid provider key
surfaces as a defect (`CONFIGURATION_ERROR` in `error_message`).

**Reprocessing after an outage or a fix.** `POST
/api/v1/workspaces/{ws}/documents/{id}/reprocess` restarts a `FAILED` document
with a fresh budget. For many documents, find them with:

```sql
SELECT document_id FROM document_versions
 WHERE is_current AND status = 'failed'
   AND failure_code IN ('PROCESSING_RETRIES_EXHAUSTED', 'INTERNAL_PROCESSING_ERROR');
```

**Poison documents.** A document that kills its worker every time (memory or hard
limit) is recorded `WORKER_LOST` per attempt and fails with
`PROCESSING_INTERRUPTED` once the budget is spent. It never loops forever.

**Deploying a new worker version.** Stop workers with a warm shutdown (`SIGTERM`);
in-flight tasks finish. A task killed before finishing is recovered by lease
expiry. Nothing needs draining in the database.
