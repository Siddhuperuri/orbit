# Operational runbook

How to investigate a failure from what ORBIT already recorded. Signals and
their meaning: [observability.md](observability.md). Dependency behaviour:
[failure-modes.md](failure-modes.md). Worker internals: [worker.md](worker.md).

Log queries below are written as `field=value` against JSON logs; translate to
your log store (`jq`, Loki, CloudWatch, …). SQL runs in `npm run infra:psql` or
against production PostgreSQL. `:document_id` etc. are values you supply.

## 0. Triage

Start here whatever the report.

1. **Get an identifier.** A user quoting an error has a `request_id` (in the
   error body, and the `X-Request-ID` header). Otherwise a `document_id` or
   `workspace_id`.
2. **Is it one request or all of them?** `orbit_http_requests_total` by
   `status`, and `orbit_http_errors_total` by `code`. One code dominating names
   the failing subsystem:

   | `code` | Go to |
   |---|---|
   | `STORAGE_UNAVAILABLE`, `STORED_OBJECT_NOT_FOUND` | §1 |
   | `DATABASE_UNAVAILABLE` | §7 |
   | `QUEUE_UNAVAILABLE` | §2 (jobs are safe; see there) |
   | `AI_PROVIDER_*`, `GENERATION_*`, `RETRIEVAL_FAILED` | §4–§6 |
   | `INTERNAL_ERROR` | A bug — §0.3 |
   | `RATE_LIMITED` | Working as designed; is it abuse? `orbit_rate_limit_decisions_total` |

3. **Follow the id.** `request_id=<id>` returns every record of that request; for
   uploaded documents it also returns the worker's attempts. For a bug, the
   `request.unhandled_exception` record holds the traceback.
4. **Check dependencies.** `GET /readyz` (per-dependency `status`, `latency_ms`,
   `critical`) or `orbit_dependency_up`. A dependency reads `down` → its section.
5. **Did something change?** `orbit_build_info{version}` against when it began.

`operation` tells you which unit of work a record belongs to. `failed_stage`,
`stages_ms`, `duration_ms`, and `error_code` are on the failure record itself —
read them before anything else.

---

## 1. A failed upload

**Symptom.** The user's upload was rejected, or the document exists but is
`failed`/never `ready`. First decide **which half failed**, because they have
different causes:

- *The upload request* (bytes → storage → database) failed → the user got an
  error immediately. §1.1.
- *Processing* (parse → embed → index) failed → the upload returned `202`, the
  document later shows `failed`. §3.

### 1.1 The request failed

```
request_id=<id>          # or: operation=documents.upload_document status_code>=400
```

Read `http.request` (`status_code`, `duration_ms`, `response_incomplete`) and
the error record beside it (`request.rejected` / `request.failed`,
`error_code`).

| `error_code` / status | Cause | Action |
|---|---|---|
| `UPLOAD_TOO_LARGE` 413 | Over `ORBIT_MAX_UPLOAD_BYTES` | Expected; user-facing |
| `UNSUPPORTED_CONTENT_TYPE` 415 | Type not accepted | Expected |
| `RATE_LIMITED` 429 | `upload` scope exceeded | Check `orbit_rate_limit_decisions_total{scope="upload"}`; abuse or a batch importer? |
| `STORAGE_UNAVAILABLE` 503 | Object storage failed **before** any row was written | Below |
| `DATABASE_UNAVAILABLE` 503 | Bytes stored, rows not committed | §7. An orphaned object is left; `SweepOrphanedStorage` reclaims it — not data loss |
| `response_incomplete=true` | Client disconnected mid-upload | Nothing to fix; check the client or a proxy timeout |

**Storage.** Uploads are written to storage *before* the database, so a storage
failure leaves nothing behind (failure-modes §3). Find whether storage is slow or
down:

- `orbit_dependency_up{dependency="object_storage"}` and `/readyz`.
- `orbit_storage_operation_duration_seconds` by `operation`, `outcome`: which
  call (`put_object`, `upload_part`, `complete_multipart`) is failing or slow?
  This histogram **excludes** the client's own upload time, so a slow value here
  is the store's.
- Logs: `storage.multipart_abort_failed` (an incomplete multipart upload may
  remain and be billed).
- Cause order of likelihood, from `head_bucket`: endpoint → credentials → bucket.

**A `202` upload the user says vanished.** `SELECT` the document (§3.1 query). If
the row exists, it is a processing question. If it does not, the request failed
before commit — the access log for that user around that time shows why.

---

## 2. A stuck processing job

**Symptom.** A document has been `pending` or `processing` far longer than usual.

**Expectation.** Normal is seconds to a few minutes. A document may be
legitimately waiting: behind a backlog, in a retry backoff, or during a provider
outage (it stays `pending`; it is *not* marked failed).

**Step 1 — is it the document or the fleet?**

| Metric | Reading | Means |
|---|---|---|
| `orbit_queue_oldest_ready_job_age_seconds` | rising | Due jobs are not being picked up |
| `orbit_queue_jobs{state="ready"}` | high and growing | Backlog: workers slow, few, or down |
| `orbit_queue_jobs{state="lease_expired"}` | > 0 | Workers died holding jobs; **recovery has not run** — is `beat` running? |
| `orbit_queue_jobs{state="scheduled"}` | high | Many jobs in retry backoff (a dependency is failing) |
| `orbit_queue_jobs` reads **NaN** | | The API cannot read the database; §7 |

Rising `orbit_job_queue_wait_seconds` with idle CPU on workers points at the
broker or worker connectivity; with busy workers, add capacity.

**Step 2 — this document.** (`:document_id` from the user or the UI.)

```sql
SELECT j.attempt, j.run_attempt, j.status, j.stage, j.scheduled_for, j.enqueued_at,
       j.lease_expires_at, j.worker_id, j.error_code, j.failure_kind, j.error_message,
       j.request_id
  FROM document_processing_jobs j
  JOIN document_versions v ON v.id = j.document_version_id
 WHERE v.document_id = :document_id AND v.is_current
 ORDER BY j.attempt;
```

| Job row | Meaning | Do |
|---|---|---|
| `queued`, `scheduled_for` in the future | Waiting out a backoff | Read the previous attempt's `error_code` — that is the real problem |
| `queued`, due, `enqueued_at` old or null | The message was lost or never published | `processing.enqueue_failed` in the API log? Force: `npm run worker:recover` |
| `running`, `lease_expires_at` **past** | Worker died | Recovery rescues it on the next sweep or worker start; `npm run worker:recover` to force |
| `running`, lease **future** | A worker is on it | Continue below |

**Step 3 — a worker has it.** Every worker record carries the job:

```
job_id=<id>
```

Read the last `pipeline.stage_completed` (`stage`, `duration_ms`); the stage
*after* it is where it is now (`j.stage` says the same). A long-running stage:

- `parse` on a large or hostile PDF — the soft limit
  (`ORBIT_PROCESSING_TASK_SOFT_TIME_LIMIT_SECONDS`) ends it as
  `DOCUMENT_PROCESSING_TIMEOUT`; the hard limit kills it and it is recovered as
  `WORKER_LOST`.
- `embed` — the provider is slow or throttling: §3 (failed embedding), §6.
- `index` — a slow or contended database: §7.

A document that kills its worker every time (`WORKER_LOST` on each attempt)
fails with `PROCESSING_INTERRUPTED` once the budget is spent; it does not loop.

`worker.md` covers limits, retries and reprocessing after a fix.

---

## 3. A failed embedding (or any processing failure)

**Symptom.** Document `failed`, or repeatedly retrying.

**3.1 Read the outcome.** Same query as §2 step 2. The important columns:
`error_code`, `failure_kind`, `error_message` (operator detail; the user sees only
a safe message).

| `failure_kind` | Meaning | Retries? |
|---|---|---|
| `transient` | A dependency failed | Yes, with backoff, until `ORBIT_PROCESSING_MAX_ATTEMPTS` |
| `permanent` | The document is at fault (`DOCUMENT_CORRUPT`, `_ENCRYPTED`, `_EMPTY`, …) | No |
| `defect` | An ORBIT bug or misconfiguration | No — page |

**3.2 Find where it failed.** The worker record for that attempt:

```
job_id=<id> event=pipeline.attempt_failed
```

`failed_stage` (e.g. `embed`), `stages_ms` (how long earlier stages took — proof
the file itself parsed), `error_code`, `run_attempt`. For a defect the record has
the traceback.

**3.3 Embedding-specific.**

| `error_code` | Cause | Action |
|---|---|---|
| `AI_PROVIDER_UNAVAILABLE` / `_TIMEOUT` / `_RATE_LIMITED` / `_RESPONSE_INVALID` | Provider trouble | §6. The document stays `pending` and retries; nothing to do per document. |
| `CONFIGURATION_ERROR` (in `error_message`: credentials rejected, quota exhausted, unknown model) | Misconfiguration — **retrying cannot help** | Fix the key/quota/model, restart workers, then reprocess |
| Embedding width mismatch / `readiness.embedding_schema_mismatch` | `ORBIT_EMBEDDING_DIMENSIONS` ≠ vector column | Pages. See docs/database/embeddings.md; readiness will hold the instance out |

Correlate with the provider. On a failed call the API and worker record
`ai.request_failed` with `call=embed_documents`, `status`, `outcome`, and
**`provider_request_id`** — quote that to the provider's support.
`orbit_ai_request_duration_seconds{operation="embed_documents"}` split by
`outcome` shows the failure mode over time; `orbit_ai_retries_total` shows
whether in-process retries were absorbing it.

**3.4 After a fix.** `POST /api/v1/workspaces/{ws}/documents/{id}/reprocess`
restarts a `failed` document with a fresh budget. For many:

```sql
SELECT document_id FROM document_versions
 WHERE is_current AND status = 'failed'
   AND failure_code IN ('PROCESSING_RETRIES_EXHAUSTED', 'INTERNAL_PROCESSING_ERROR');
```

**Everything failing at once?** Group `orbit_processing_failures_total` by
`error_code`: one code = one cause. `defect` = a bug or config; `transient` = a
dependency.

---

## 4. Slow search

**Symptom.** Search is slow. Start with the numbers, not the guess.

1. `orbit_search_duration_seconds` p95 by `mode` — is it all searches or one mode?
   `outcome="degraded"` means the semantic half is failing and the search is
   *lexical only* (§6).
2. **Which stage?** `orbit_search_stage_duration_seconds` by `stage`:

   | Slow stage | Cause | Check |
   |---|---|---|
   | `query_embedding` | Provider latency, or the cache is cold/failing | `orbit_ai_request_duration_seconds{operation="embed_query"}`; `orbit_cache_requests_total{cache="query_vector"}` — hit ratio low, or `error` > 0 (Redis down: every query pays the provider) |
   | `lexical` | Full-text scan | `db.slow_query` records; is a filter forcing a scan? Is the GIN index there (`ix_chunks_search_vector`)? |
   | `semantic` | Vector index | `db.slow_query`; `ef_search`; index built? See docs/database/indexes.md and docs/benchmarks/ |
   | `hydrate` | Loading result rows | `db.slow_query` (`select`) |

3. `db.slow_query` log records give the statement (placeholders only),
   `duration_ms`, and — via `request_id` — the search that ran it. To see a
   statement live:

   ```sql
   SELECT application_name, state, wait_event_type, now() - query_start AS age, query
   FROM pg_stat_activity WHERE datname = current_database() ORDER BY age DESC;
   ```
4. Pool: `orbit_db_pool_connections{state="in_use"}` near `orbit_db_pool_limit`
   means requests are *queueing for a connection* — the database may be fine and
   the pool too small, or held by long queries.
5. One workspace only? Find it from the slow `http.request` lines
   (`operation=search.search`, sort by `duration_ms`, group by `workspace_id`);
   a very large or highly filtered workspace behaves differently.

Per-request detail: `search.completed` (`mode`, `retrievers`, `degraded`,
candidate counts, `results`) tells you what the search did.

---

## 5. A slow RAG (answer) request

**Symptom.** Questions take too long, or the answer starts late.

1. The `answer.finished` record (or the message's stored `metrics`) already
   splits the time: `retrieval_ms`, `first_token_ms`, `generation_ms`,
   `total_ms`, plus `context_tokens`, `retrieved`, `prompt_tokens`,
   `completion_tokens`. Across traffic: `orbit_rag_duration_seconds` by `stage`.

   | Slow part | Meaning | Go to |
   |---|---|---|
   | `retrieval` | Search is slow | §4 (it *is* a search, `operation=search.execute`) |
   | `first_token` | Provider is slow to start, or the prompt is huge | `orbit_llm_first_token_seconds`; `context_tokens` (large context = slow start) |
   | `generation` | Long output or a slow provider | `orbit_ai_request_duration_seconds{operation="chat_stream"}`; `completion_tokens` |

2. HTTP-level duration is to the **end of the stream**, so
   `orbit_http_request_duration_seconds` for the chat route is the whole answer.
3. Failures instead of slowness: `orbit_rag_answers_total` by `status` and
   `stop_reason` (`timeout`, `provider_error`, `retrieval_failed`,
   `max_tokens`). `RETRIEVAL_FAILED` and `GENERATION_FAILED` are different
   stages on purpose — a retrieval failure is §4/§7, a generation failure is §6.
4. Poor answers, not slow ones: `grounding="uncited"`, and
   `orbit_citations_total{result="discarded"}` rising — check `prompt_version`
   and `model_id` in `answer.finished`; a model or prompt change is the usual
   cause.
5. A user reports a specific answer: `request_id` finds it; `message_id` in
   `answer.finished` maps to the stored message.

---

## 6. AI provider outage

**Symptoms.** Search returns `degraded`; questions fail `GENERATION_FAILED`;
documents sit `pending`.

**Confirm it is the provider, not us.**

- `orbit_ai_request_duration_seconds` by `outcome`: a jump in `unavailable`,
  `timeout`, or `rate_limited`. `config_error` = **our** credentials, quota, or
  model, not an outage.
- `orbit_ai_retries_total` climbing first, then failures = a provider
  degrading; failures with no retries = it went hard down.
- `orbit_llm_circuit_open == 1` (and `/readyz` shows `language_model` down) =
  questions are being failed fast until a probe succeeds after
  `ORBIT_LLM_CIRCUIT_RESET_SECONDS`.
- `ai.request_failed` records: `status`, `outcome`, `provider_request_id`.
  Compare with the provider's status page; give support the ids.

**What degrades, and what does not** (failure-modes §5–§6):

| Feature | Behaviour |
|---|---|
| Search | Serves lexical results, response `degraded: "semantic_unavailable"`. A caller that asked for `mode: "semantic"` gets `503` |
| Questions | `503 GENERATION_FAILED` (or `_TIMEOUT`, `_RATE_LIMITED`); retryable; search is unaffected |
| Processing | Documents stay `pending` and retry with backoff; **none are marked failed** for a provider outage |
| Login, upload, browse | Unaffected |

**Act.**

1. Nothing to restart. Cached query vectors still serve searches without a call.
2. If it is `rate_limited` and the cause is us (a large reindex, a backlog
   drain), reduce worker concurrency — the retry policy already honours
   `Retry-After`.
3. If it is `config_error` (401/403/404, exhausted quota): fix the key, quota, or
   model name and restart. Affected documents (`CONFIGURATION_ERROR`) need
   `reprocess`.
4. After recovery, backlog drains on its own. `npm run worker:index-status`
   reports embedding coverage; exit `3` while stale chunks remain.
5. Long outage: pending documents are safe indefinitely — retries are bounded by
   `ORBIT_PROCESSING_MAX_ATTEMPTS`, after which they fail with a reason and can
   be reprocessed in bulk (§3.4).

---

## 7. Database outage

**Symptoms.** `/readyz` `503` (`database` down), `orbit_dependency_up{dependency="database"} == 0`,
`DATABASE_UNAVAILABLE` errors, `orbit_queue_jobs` reading NaN.

**Expected behaviour.** Instances leave rotation (readiness `not_ready`; they are
*not* restarted — liveness never checks the database). There is no degraded
mode: PostgreSQL is the source of truth. Login, browse, search, chat, upload all
fail. Workers cannot record outcomes and log `task.outcome_not_recorded`
(`orbit_task_outcome_not_recorded_total`); the messages are redelivered
(bounded), and jobs are still recovered from the database afterwards. Uploads
that had already stored bytes leave orphaned objects that the sweep reclaims.

**Distinguish the cause.**

| Signal | Points to |
|---|---|
| `orbit_db_errors_total{kind="connection"}` | Cannot connect: network, DNS, credentials, database down |
| `{kind="statement_timeout"}` | Reachable but overloaded or a runaway query (15 s API / 120 s worker) |
| `{kind="deadlock"}` | Contention — usually a deploy or a new code path |
| `in_use == limit` on `orbit_db_pool_connections`, DB otherwise fine | Pool exhausted, not an outage |
| `db.slow_query` volume up | Load or a bad plan; check the statements |
| `DATABASE_UNAVAILABLE` but `readyz` database `up` | Pool exhaustion or intermittent failures — look at pool and slow queries |

**Investigate** (from a host that can still reach PostgreSQL):

```sql
-- what is running, and who (application_name is orbit-api / orbit-worker)
SELECT application_name, state, wait_event_type, now() - query_start AS age, query
FROM pg_stat_activity WHERE datname = current_database() ORDER BY age DESC;

-- blocked queries
SELECT pid, pg_blocking_pids(pid) AS blocked_by, now() - query_start AS age, query
FROM pg_stat_activity WHERE cardinality(pg_blocking_pids(pid)) > 0;
```

If the pool is exhausted but PostgreSQL is healthy, look for a query older than
the statement timeout — that means the timeout is not applied; connect args apply
at connection time, so restart after changing it.

**Recover.** Restore the database; instances rejoin as `/readyz` turns green. Then:

1. `npm run worker:recover` (or wait for the scheduled sweep) to rescue jobs a
   worker held when it lost the database and to re-publish lost messages.
2. `orbit_queue_jobs{state="lease_expired"}` should fall to 0 and
   `orbit_queue_oldest_ready_job_age_seconds` to drain.
3. Answers that were streaming when the database failed finish but may fail to
   save (`answer.not_saved`); they are counted in `orbit_rag_answers_total`
   regardless. A message left `pending` by a request that died is later closed as `ANSWER_ABANDONED`.
4. Nothing to replay or flush. Redis and object storage were not involved.

---

## Appendix: reading one document's whole story

```
request_id=<id from the upload's X-Request-ID, or from job.request_id>
```

returns, in order: the upload's `http.request` (who — `user_id`; what —
`operation`; how long), `job.claimed` for each attempt, each
`pipeline.stage_completed` with its duration, `pipeline.attempt_failed` /
`job.retry_scheduled` with reasons and delays, and `pipeline.completed` or
`job.failed_permanently`. If you only have the document:

```sql
SELECT j.request_id FROM document_processing_jobs j
  JOIN document_versions v ON v.id = j.document_version_id
 WHERE v.document_id = :document_id ORDER BY j.attempt LIMIT 1;
```
