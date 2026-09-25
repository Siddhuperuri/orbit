# Observability

What ORBIT records about itself, how to reach it, and what each signal is for.
Design and trade-offs: [ADR-0015](../decisions/0015-observability-strategy.md).
Using it during an incident: [runbook.md](runbook.md).

The goal is a specific one: **an engineer can diagnose a failure from what the
system already recorded, without reproducing it.** Every section below exists
to serve that.

---

## 1. Logs

JSON on stderr in every deployed environment (`ORBIT_LOG_FORMAT=json`),
human-readable locally. One event per line, one `event` name per kind of
occurrence, so a query is a field lookup, never a regex.

### Fields on every record

| Field | Meaning |
|---|---|
| `timestamp`, `level`, `logger` | When, how severe, which module |
| `service`, `environment`, `version` | Which process and build wrote it (`orbit-api`, `orbit-worker`, …) |
| `request_id` | The HTTP request that started this work. **Survives the queue** — see below |
| `user_id` | The authenticated actor (API records after authentication) |
| `workspace_id`, `document_id` | Tenant and document, from the route or, in the worker, from the job |
| `job_id` | The processing-job attempt (worker records) |
| `operation` | The unit of work: `documents.upload_document`, `document.process`, `job.recover`, `search.execute`. A small, code-derived vocabulary — never user input |

A field is present when it applies. `operation` is what to group by;
`request_id` is what to follow.

### One request, across processes

```
browser ─▶ API ─▶ Postgres (job row: request_id)
             │
             └──▶ Redis/Celery ─▶ worker ─▶ OpenAI
                  kwargs: job_id, request_id         header: X-Client-Request-Id
```

- The API assigns `request_id` (a ULID) in the outermost middleware and returns
  it in `X-Request-ID` — on every response, including a `500`.
- It is written to the job row and carried in the Celery payload. **Every
  attempt and every retry of that job logs under the upload's `request_id`.**
  `request_id = X` therefore returns the upload request, each worker attempt,
  and the outcome, in order.
- The worker adds `job_id` on receipt, and `workspace_id` / `document_id` once
  it has claimed the job.
- Outbound calls send `X-Client-Request-Id: <request_id>` to the AI provider. On
  a failure the provider's own `x-request-id` is captured as
  `provider_request_id`, so a provider support ticket can quote it.
- A client-supplied `X-Request-ID` is honoured **only in production behind a
  trusted proxy**, and only if it is a valid ULID.

`user_id` is not carried into the worker: a job belongs to a workspace, not a
person. To find who uploaded a document, look up the `http.request` line with
the same `request_id`.

### The access log

One `http.request` line per request (not for `/healthz`, `/readyz`):
`method`, `path`, `route` (the template), `status_code`, `duration_ms`, plus the
correlation fields above. It is emitted when the response is **finished**, so a
streamed answer is timed to its last byte. Levels: `info`; `warning` for a
`5xx` or a request over `ORBIT_SLOW_REQUEST_THRESHOLD_MS` (`slow=true`).
`response_incomplete=true` means headers went out but the body did not finish (a
client that disconnected, or a stream that failed midway).

### What is never logged

Enforced by a processor, not by care: the values of `password`, `token`,
`authorization`, `cookie`, `secret`, `api_key`, `presigned_url`, `signature`,
and — critically — `content`, `text`, `chunk_text`, at any nesting depth.
Document text and prompts are never log fields; a length or a hash is.
Provider error bodies are dropped (they can echo the prompt); only the status,
error code, and provider request id are kept. SQL in slow-query records is the
statement with placeholders, never bound values. Tests cover each of these
(`tests/unit/observability`).

### Events worth knowing

| Event | Level | Means |
|---|---|---|
| `http.request` | info/warning | The access line |
| `request.failed` | error | A deliberate 5xx (`error_code` says which) |
| `request.unhandled_exception` | error | A **bug**; traceback included, same `request_id` the client was shown |
| `db.slow_query` | warning | Statement over `ORBIT_DB_SLOW_QUERY_THRESHOLD_MS` |
| `ai.request_failed` | warning | One provider call failed: `call`, `outcome`, `status`, `provider_request_id`, `duration_ms` |
| `search.degraded` | warning | Semantic half unavailable; served lexical-only |
| `answer.finished` | info/warning | One answer: status, stop reason, grounding, per-stage ms, tokens |
| `answer.citations_discarded` | warning | The model cited sources it was not given |
| `pipeline.stage_completed` | info | A stage finished, with `duration_ms` and output counts |
| `pipeline.attempt_failed` | warning/error | An attempt failed: `error_code`, `failed_stage`, `stages_ms`; **error** level = defect |
| `job.retry_scheduled`, `job.failed_permanently` | warning/error | Retry / terminal outcome |
| `job.abandoned`, `job.redelivered`, `job.lease_lost` | warning | Recovery activity |
| `processing.enqueue_failed` | error | Publish to the broker failed after commit |
| `ratelimit.backend_unavailable` | error | Redis down; limiter **failed open** |
| `metrics.refresh_failed` | warning | Queue/pool gauges could not be refreshed (they read NaN) |

Worker-specific events and their alerting: [worker.md](worker.md).

---

## 2. Health endpoints

| Endpoint | Question | Checks | Status |
|---|---|---|---|
| `GET /healthz` | Is the process alive? | Nothing external | Always `200` while running |
| `GET /readyz` | Should it receive traffic? | PostgreSQL, vector schema, Redis, object storage, language-model circuit | `200` ready/degraded, `503` not ready |

**Liveness never checks a dependency.** A liveness probe that touched the
database would restart every instance during a database blip.

**Readiness distinguishes critical from non-critical dependencies:**

| Dependency | Critical | Why |
|---|---|---|
| `database` | yes | Identity, authorization, documents, and job state; no degraded mode |
| `embedding_schema` | yes | A dimension mismatch silently corrupts rankings (ADR-0007) |
| `redis` | no | Broker, limiter, cache — each fails open or defers (ADR-0024) |
| `object_storage` | no | Uploads/downloads fail with a retryable 503; everything else works |
| `language_model` | no | Reports the circuit breaker's state (passive: **no paid call is made**) |

Status is `ready`, `degraded` (a non-critical dependency is down; still `200`, so
the instance stays in rotation), or `not_ready` (`503`). Each dependency reports
`status`, `latency_ms`, `critical`, and a generic `detail` — never a host,
credential, or driver message. Probes run concurrently under a 2 s timeout; a
slow dependency is an unready one, and a probe that throws counts as down.

The AI provider is deliberately **not actively probed**: doing so costs money on
every poll from every instance, and would couple readiness to a third party.

The worker has no HTTP surface. Its liveness is `celery inspect ping`; its
health is the queue metrics below.

---

## 3. Metrics

Prometheus, on a **separate port** — never the public API port.

| Process | Default | Setting |
|---|---|---|
| API | `127.0.0.1:9100` | `ORBIT_METRICS_HOST`, `ORBIT_METRICS_PORT` |
| Worker | `127.0.0.1:9101` | `ORBIT_METRICS_HOST`, `ORBIT_WORKER_METRICS_PORT` |

`ORBIT_METRICS_ENABLED=false` removes the endpoint (counters are still kept in
process). In a container set `ORBIT_METRICS_HOST=0.0.0.0` **and do not route the
port from the ingress** — that is the whole access control. Add it to the
deployment checklist.

**Multiple processes.** The Celery worker is prefork; uvicorn may run several
workers. Set `PROMETHEUS_MULTIPROC_DIR` to an empty, writable directory *before
start* (compose does this with a tmpfs) and a scrape merges every process's
samples. A prefork worker without it refuses to serve metrics and logs
`metrics.prefork_requires_multiproc_dir` rather than exporting a misleading flat
line. Without it, a multi-worker API serves only the process that won the port.

**Labels are bounded.** No `user_id`, `workspace_id`, `document_id`, job id, or
raw URL appears in any label (a test enforces it). Routes are *templates*;
requests matching no route share one `unmatched` series so a scanner cannot mint
series. Identifiers are for logs.

Every metric below has a reason to exist. If you cannot say what you would do
with a series, it should not be here — `tests/unit/observability/test_metrics.py`
requires each metric to state the question it answers.

### HTTP

| Metric | Type | Labels | Answers |
|---|---|---|---|
| `orbit_http_requests_total` | counter | method, route, status | Traffic; 5xx rate by endpoint |
| `orbit_http_request_duration_seconds` | histogram | method, route | Which endpoint is slow (to last byte, so streams are honest) |
| `orbit_http_requests_in_flight` | gauge | — | Saturated or hung, independent of latency |
| `orbit_http_errors_total` | counter | code | *Which* failure: `STORAGE_UNAVAILABLE` vs `GENERATION_FAILED` vs `INTERNAL_ERROR` |

### Database

| Metric | Type | Labels | Answers |
|---|---|---|---|
| `orbit_db_query_duration_seconds` | histogram | operation, outcome | Is the database slow; reads or writes |
| `orbit_db_errors_total` | counter | kind | `statement_timeout`, `deadlock`, `connection`, `other` (constraint violations excluded) |
| `orbit_db_pool_connections`, `orbit_db_pool_limit` | gauge | state | Pool saturation = `in_use / limit` |

### Queue and document processing

| Metric | Type | Labels | Answers |
|---|---|---|---|
| `orbit_queue_jobs` | gauge | state | `ready`, `scheduled` (backoff), `running`, `lease_expired` (**abandoned**) — from PostgreSQL |
| `orbit_queue_oldest_ready_job_age_seconds` | gauge | — | How long the longest-waiting due job has waited |
| `orbit_queue_publish_total` | counter | outcome | Can the API reach the broker |
| `orbit_job_queue_wait_seconds` | histogram | — | Queueing latency users experience |
| `orbit_job_executions_total` | counter | outcome | succeeded / retry_scheduled / failed / skipped / lease_lost |
| `orbit_processing_failures_total` | counter | error_code, failure_kind, terminal | *Why* documents fail |
| `orbit_job_recoveries_total` | counter | kind | `abandoned` (worker died), `redelivered` (message lost) |
| `orbit_task_outcome_not_recorded_total` | counter | — | Worker cannot reach the DB — pages |
| `orbit_document_processing_duration_seconds` | histogram | format, outcome | Processing time, claim → outcome |
| `orbit_document_ready_latency_seconds` | histogram | — | **Upload → READY** (first run only; reprocesses excluded) |
| `orbit_pipeline_stage_duration_seconds` | histogram | stage, outcome | Where processing time goes; a stage that failed still records its time |

The queue gauges are refreshed from the database every
`ORBIT_METRICS_REFRESH_INTERVAL_SECONDS` (15) by the **API** process. They read
`NaN` — not the last value — if the refresh fails, so a database outage never
shows as "no backlog". With N API replicas each reports the same fact; the
gauges use `max` aggregation across processes, and dashboards should use
`max()`, not `sum()`.

### Retrieval and answering

| Metric | Type | Labels | Answers |
|---|---|---|---|
| `orbit_search_duration_seconds` | histogram | mode, outcome | Search latency; `ok` / `degraded` / `rejected` / `error` |
| `orbit_search_stage_duration_seconds` | histogram | stage | Slow *where*: `query_embedding`, `lexical`, `semantic`, `hydrate` |
| `orbit_search_degraded_total` | counter | reason | Traffic getting lexical-only results |
| `orbit_rag_answers_total` | counter | status, stop_reason, grounding | Answered, failed, timed out, uncited |
| `orbit_rag_duration_seconds` | histogram | stage | `retrieval`, `first_token`, `generation`, `total` |
| `orbit_citations_total` | counter | result | Discarded share = model/prompt regression (ADR-0006) |

### AI providers

| Metric | Type | Labels | Answers |
|---|---|---|---|
| `orbit_ai_request_duration_seconds` | histogram | provider, operation, outcome | One HTTP call to the provider; outcome: `ok`, `rate_limited`, `timeout`, `unavailable`, `invalid_response`, `config_error`, `cancelled`, `error`. Retry sleeps excluded |
| `orbit_ai_tokens_total` | counter | operation, kind | What it costs |
| `orbit_ai_retries_total` | counter | operation, reason | Retries rise **before** failures do |
| `orbit_llm_first_token_seconds` | histogram | — | How long a user stares at an empty answer |
| `orbit_llm_circuit_open` | gauge | — | Fast-failing because the provider is down |

`operation` is `embed_documents` (pipeline), `embed_query` (search),
`chat_complete`, `chat_stream`.

### Cache, rate limiting, storage, dependencies

| Metric | Type | Labels | Answers |
|---|---|---|---|
| `orbit_cache_requests_total` | counter | cache, result | hit / miss / **error** (Redis failing open — not a cold cache) |
| `orbit_rate_limit_decisions_total` | counter | scope, decision | `rejected` = abuse; `unavailable` = **limiter failed open, protection absent** |
| `orbit_storage_operation_duration_seconds` | histogram | operation, outcome | One S3 call, excluding time waiting on the client's upload |
| `orbit_dependency_up` | gauge | dependency | Which dependency is down, as a time series |
| `orbit_dependency_check_duration_seconds` | histogram | dependency | A dependency getting slow before it is down |
| `orbit_build_info` | gauge | service, version, environment | Did it start with a deploy |

---

## 4. Suggested alerts

Thresholds are starting points to tune against real traffic, not measurements.

| Alert | Condition | Why | First step |
|---|---|---|---|
| API 5xx | `5xx / all` > 2 % for 5 m | Users failing | Runbook §Triage; group `orbit_http_errors_total` by `code` |
| **Defect** | `orbit_http_errors_total{code="INTERNAL_ERROR"}` > 0, or `processing_failures{failure_kind="defect"}` > 0 | A bug | Log: `request.unhandled_exception` / `pipeline.attempt_failed` at error |
| Database down | `orbit_dependency_up{dependency="database"} == 0` for 1 m | Everything stops | §7 |
| Pool saturated | `in_use / limit` > 0.9 for 5 m | Latency cliff | §4 slow search; look for long queries |
| Worker starved | `orbit_queue_oldest_ready_job_age_seconds` > 300 for 5 m | Documents waiting | §2 stuck job |
| Abandoned jobs | `orbit_queue_jobs{state="lease_expired"}` > 0 for 10 m | Recovery not running | Is `beat` running? |
| Queue unknown | `orbit_queue_jobs` is NaN for 5 m | API cannot read the DB | §7 |
| Processing failing | terminal failures / executions > 20 % for 15 m | Pipeline broken | §3 |
| **Worker blind** | `orbit_task_outcome_not_recorded_total` rising | Worker cannot record outcomes | §7 |
| Provider degrading | `orbit_ai_retries_total` rate up, or `ai_request_duration{outcome!="ok"}` share > 10 % | Provider trouble | §6 |
| Circuit open | `orbit_llm_circuit_open == 1` | Questions failing fast | §6 |
| Citations regressing | `discarded / (discarded + resolved)` up sharply | Model/prompt regression | Check `prompt_version`, `model_id` in `answer.finished` |
| **Limiter failing open** | `rate_limit_decisions{decision="unavailable"}` > 0 | No brute-force protection | Restore Redis; edge limits meanwhile |
| Ready latency (SLO) | p95 `orbit_document_ready_latency_seconds` above target | Users waiting | §2 |
| Search slow | p95 `orbit_search_duration_seconds` above target | | §4 |

## 5. Local use

```bash
npm run dev:api        # logs to console; metrics on 127.0.0.1:9100
npm run dev:worker     # metrics on 127.0.0.1:9101 (solo pool on Windows)
curl -s 127.0.0.1:9100/metrics | grep '^orbit_'
```

Nothing scrapes locally; the endpoint exists so it is exercised. Compose
publishes both ports on loopback only.

## 6. Known limits

- **Traces are not implemented** (ADR-0015): correlation is by `request_id` in
  logs. Adopting OpenTelemetry later changes the emitter, not the call sites.
- **No error-tracking service** is wired. Defects are ERROR logs with a
  traceback and an `INTERNAL_ERROR` counter; the `ErrorReporter` port from
  ADR-0015 is not yet built.
- **Statement timeouts and pool checkout timeouts are not separate series.**
  A statement timeout is `orbit_db_errors_total{kind="statement_timeout"}`; pool
  exhaustion appears as `DATABASE_UNAVAILABLE` in `orbit_http_errors_total` with
  `in_use == limit`.
- **Without `PROMETHEUS_MULTIPROC_DIR`, a multi-process API under-reports.**
- **Provider outcome `cancelled`** covers a client that disconnected mid-answer;
  it says nothing about the provider and should not be alerted on.
- The metrics HTTP server is `prometheus_client`'s: unauthenticated by design.
  Network placement is the control.
