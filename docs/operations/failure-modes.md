# Failure modes

What ORBIT does when a dependency fails, why it does that, and how to tell
which one you are looking at.

Every row in this document has a test behind it in
`backend/tests/integration/test_failure_modes.py` or
`backend/tests/unit/retrieval/test_search_resilience.py`. If a claim here is
wrong, a test should be failing; if it is not, the test is the thing to fix
first.

The design rule these follow: **a dependency failure degrades the feature that
needs it and nothing else.** An outage should never take down a code path that
did not need the dead dependency in the first place.

---

## 1. At a glance

| Dependency | Down means | Still works | Client sees |
|---|---|---|---|
| **PostgreSQL** | Nothing works | Nothing | `/readyz` returns `503`, instance leaves rotation |
| **Object storage** | No uploads, no downloads | Browsing, search, chat, all metadata | `503 STORAGE_UNAVAILABLE`, retryable |
| **Redis** | Slower search, unmetered rates, delayed processing | Everything, including login and upload | Nothing — the degradation is invisible to clients by design |
| **Embedding provider** | No semantic retrieval, no new indexing | Search (lexical half), everything else | Results with `degraded: "semantic_unavailable"` |
| **Language model** | No answers | Search, upload, browsing | `503 GENERATION_FAILED`, retryable |
| **Outbound email** | No reset or verification links | Everything else | `503` on the request that needed mail |

---

**Readiness follows this table.** `/readyz` marks PostgreSQL and the vector schema
*critical* and everything else non-critical: losing Redis, object storage, or
the language model reports `degraded` with status `200`, so the instance stays in
rotation and keeps serving what does not need them. Before this, any failed
dependency returned `503` -- which would have taken every instance out of
rotation during a Redis outage and made "Redis is disposable" untrue.

## 2. PostgreSQL

**Behaviour.** The instance reports not-ready and leaves rotation. There is no
degraded mode, and that is the correct design: PostgreSQL is the source of
truth for identity, authorization, documents, and job state. An instance that
kept serving without it would serve wrong answers.

**Statement timeouts.** Every connection carries a server-side
`statement_timeout` — 15 s for the API, 120 s for the worker
(`ORBIT_DB_STATEMENT_TIMEOUT_SECONDS`,
`ORBIT_WORKER_DB_STATEMENT_TIMEOUT_SECONDS`). This is not a nicety. A
client-side deadline stops the waiting coroutine but leaves the query running,
holding its locks and its pooled connection; a handful of those exhaust the
pool and stall every request in the process. Only PostgreSQL cancelling the
statement returns the resource.

**Diagnosing.** Every connection sets `application_name` from
`ORBIT_SERVICE_NAME`, so `pg_stat_activity` distinguishes API connections from
worker connections without guessing from query text:

```sql
SELECT application_name, state, wait_event_type, now() - query_start AS age, query
FROM pg_stat_activity WHERE datname = current_database() ORDER BY age DESC;
```

**If the pool is exhausted but PostgreSQL is healthy**, look for a query older
than the statement timeout. Finding one means the timeout is not being applied
— check that the process was restarted after the setting changed, since
`connect_args` apply at connection time.

---

## 3. Object storage

**Behaviour.** Uploads and downloads fail with `503 STORAGE_UNAVAILABLE`,
marked retryable. Everything that reads only PostgreSQL — listing documents,
searching, asking questions about already-indexed content — is unaffected.

**Uploads fail *clean*, which is the property that matters.** Bytes are written
to storage **before** the database rows that reference them (ADR-0011). That
ordering is chosen so a storage outage leaves *nothing*:

| Ordering | Storage fails | Database fails |
|---|---|---|
| Storage first (**ORBIT**) | No row is written. The upload failed and nothing exists | An **orphaned object**: invisible, reclaimed by `SweepOrphanedStorage` |
| Database first | A row pointing at bytes that were never stored: visible, undownloadable, and the pipeline fails on it forever | — |

An orphan costs storage. A dangling row costs correctness. Only the cheap
failure is ever risked. Verified by
`test_an_upload_fails_cleanly_and_writes_no_database_row`.

**Timeouts.** `ORBIT_S3_CONNECT_TIMEOUT_SECONDS` (3 s) and
`ORBIT_S3_READ_TIMEOUT_SECONDS` (10 s). The read timeout is per socket read,
not per operation — a large multipart upload is many reads, each of which must
individually progress, so a healthy 50 MB upload is not capped at ten seconds.

**Runbook.** Check `/readyz`: `object_storage` reports its own status. The
probe is `head_bucket`, so a failure means the endpoint, the credentials, or
the bucket — in that order of likelihood. Documents uploaded before the outage
remain searchable throughout; only their original bytes are unreachable.

---

## 4. Redis

Redis is the one dependency ORBIT is explicitly willing to lose (ADR-0024).
Nothing authoritative lives there.

| What uses it | During an outage |
|---|---|
| Celery broker | Publishing fails; the committed job row is re-published by the recovery sweep after `ORBIT_PROCESSING_REDELIVERY_GRACE_SECONDS` |
| Rate limiter | **Fails open** — every request is allowed, and each failure logs at ERROR |
| Query-vector cache | **Fails open** — every query is a miss and calls the provider |
| Celery result backend | Results unavailable; job state is in PostgreSQL regardless |

**Uploads still succeed.** This is worth stating plainly because it is the
opposite of what an earlier draft of `system.md` claimed. The durable half of
an upload does not involve Redis at all: the object is stored, and the
document, version, and processing job commit in **one transaction**. Only the
publish needs the broker, and a failed publish delays the job rather than
losing it. Refusing the upload would report a failure that did not happen and
throw away bytes the user already sent. Verified by
`test_an_upload_still_succeeds_and_leaves_a_job_for_recovery`.

**Rate limiting fails open, deliberately.** This is the uncomfortable choice
and it is argued in ADR-0017: failing closed would turn a Redis outage into a
total authentication outage, including for the operators trying to fix it.
Fail-open is only defensible because it is *visible* — it logs at ERROR, and
`/readyz` reports Redis down. Argon2id continues to make each credential guess
expensive regardless.

**During a Redis outage, treat brute-force protection as absent.** If the
outage is long and the exposure matters, the mitigation is at the edge — a
proxy-level connection limit — not in ORBIT.

**Runbook.**

- `/readyz` names `redis` as down. Log signals: `ratelimit.backend_unavailable`
  (ERROR), `querycache.unavailable` (WARNING),
  `processing.enqueue_failed` (ERROR).
- Documents uploaded during the outage sit in `pending` until recovery runs.
  They are not lost. `npm run worker:recover` forces the sweep immediately.
- Nothing needs to be flushed or replayed after recovery. Every key ORBIT
  writes itself carries a TTL — rate-limit counters expire with their window,
  cache entries with `ORBIT_QUERY_VECTOR_CACHE_TTL_SECONDS`, Celery results
  after an hour — and the broker's own queues drain as the backlog is worked.

---

## 5. Embedding provider

**Behaviour, on the read path: search degrades and says so.** A hybrid search
whose embedding call fails is answered from the lexical retriever alone, with
`retrievers: ["lexical"]` and `degraded: "semantic_unavailable"` in the
response. A caller that asked for `mode: "semantic"` explicitly gets a `503`
instead — there is no lexical answer to give them, and silently substituting
one would be a lie.

This is the degradation the brief names directly: **AI unavailable, search
still works.** Verified by
`test_search_keeps_working_without_the_embedding_provider`.

A query whose vector was cached before the outage keeps its semantic half
during it, because no provider call is needed (ADR-0024).

**Behaviour, on the write path: nothing is marked failed.** Processing retries
with durable backoff and the version stays `pending`. Marking it `failed` would
be a lie: the document is fine, the provider is not. Retries are bounded by
`ORBIT_PROCESSING_MAX_ATTEMPTS`; in-process retries
(`ORBIT_EMBEDDING_MAX_RETRIES`) absorb a brief 429 without discarding batches
already embedded, and a longer outage releases the worker rather than holding a
slot asleep (ADR-0020).

**Transient versus permanent.** The distinction is structural, not heuristic:

| Class | Errors | Treatment |
|---|---|---|
| **Transient** | `AI_PROVIDER_UNAVAILABLE`, `_TIMEOUT`, `_RATE_LIMITED`, `_RESPONSE_INVALID` | Retried in-process, then by the job's durable backoff |
| **Permanent** | `CONFIGURATION_ERROR` — rejected key, exhausted quota, a model that does not produce this space | Propagates at once. Retrying cannot change it |
| **Permanent, document-level** | `DOCUMENT_CORRUPT`, `_ENCRYPTED`, `_EMPTY`, `_LIMIT_EXCEEDED`, … | Recorded as `failed` with a reason on attempt 1. The bytes will not change |

A provider asking to wait longer than `ORBIT_EMBEDDING_RETRY_MAX_WAIT_SECONDS`
is not waited on in-process: the failure goes to the durable schedule instead
of holding a worker.

**Runbook.** `search.degraded` log lines with
`reason=semantic_unavailable` are the first signal. Check
`npm run worker:index-status` for coverage; documents that piled up as
`pending` drain on their own once the provider returns.

---

## 6. Language model

**Behaviour.** Questions fail with `503 GENERATION_FAILED` (or
`GENERATION_TIMEOUT`, `GENERATION_RATE_LIMITED`). Search, upload, and browsing
are untouched — chat is the only feature that needs the model.

**Stage errors are distinct on purpose.** "We could not search your documents"
(`RETRIEVAL_FAILED`) and "we found the sources but the model did not answer"
(`GENERATION_FAILED`) need different messages, different alerts, and different
runbooks, so they never share a code (ADR-0022).

**A circuit breaker fails fast during an outage.** After
`ORBIT_LLM_CIRCUIT_FAILURE_THRESHOLD` consecutive provider failures the circuit
opens and calls are refused without being attempted, until one probe is let
through after `ORBIT_LLM_CIRCUIT_RESET_SECONDS`. Without it, every question
waits out the full timeout before failing anyway — holding a connection and a
person's attention for nothing. Rate limiting does not trip the breaker (a 429
proves the provider is alive), and neither does a cancelled call.

**A stream is retried only before its first token.** Once text has been handed
on it may already be on screen; replaying would duplicate or contradict it.
After the first event a failure propagates and whatever was produced is
recorded as a partial answer.

The breaker is per-process. A wide API fleet converges on the outage a few
requests per process at a time; see ADR-0024 for why it is not shared through
Redis.

---

## 7. Outbound email

**Behaviour.** Password reset and verification requests fail with a retryable
`503`. `ORBIT_EMAIL_PROVIDER=unconfigured` is the default and raises on every
send — deliberately, because a fake sender that swallows reset mail is the
worst possible failure: everything looks healthy and no user can ever recover
an account.

`console` is refused in production: a reset link in a log file is a credential
in a log file.

---

## 8. The API process itself

**No external failure terminates the process.** Every exception leaves through
one of the handlers in `api/errors.py`, with a catch-all registered for
`Exception`. An unanticipated exception becomes a generic `500` carrying only
the request id; the traceback is logged, never returned.

**Liveness never checks a dependency.** `/healthz` asks "is this process
alive?" and touches nothing external. A liveness probe that checked the
database would restart every instance during a database blip, converting a
recoverable dependency failure into a self-inflicted restart storm. `/readyz`
is the probe that checks dependencies, with a 2 s per-probe timeout — a slow
dependency is an unready one.

**Startup never calls a dependency.** Clients connect lazily, so a dependency
being momentarily down cannot stop a process booting — which is precisely when
you need it to boot. Whether dependencies are reachable is readiness's
question.

---

## 9. Rate limiting

Every endpoint that costs money, CPU, or an outbound message is metered per
account **and** per client address; either limit tripping rejects with `429`
and `Retry-After`.

| Scope | Endpoint | Default | Why |
|---|---|---|---|
| `login` | `POST /auth/login` | 10/account, 50/IP per 15 min | Credential guessing and stuffing |
| `register` | `POST /auth/register` | 3/account, 10/IP per hour | Argon2id is a CPU sink; a duplicate is an enumeration oracle |
| `password_reset` | `POST /auth/password-reset` | 3/account, 10/IP per hour | Each request costs an email to an address the caller chose |
| `email_verification` | resend | 3/account, 10/IP per hour | As above |
| `upload` | `POST …/documents` | 60/account, 120/IP per hour | Each upload costs a hash, an S3 write, and a row |
| `search` | `POST …/search` | 120/account, 300/IP per 5 min | Each query costs an embedding call and two index scans |
| `chat` | `POST …/messages` | 30/account, 120/IP per 5 min | Each question costs a retrieval and a model call |

Both counters are recorded even when the first already rejects: skipping the
second would let an attacker who exhausted the cheap per-account budget consume
the per-IP budget for free.

`ORBIT_TRUST_PROXY_HEADERS` defaults to **false**, and the default is the
security control. Any client can set `X-Forwarded-For`; trusting it
unconditionally would let an attacker obtain a fresh per-IP bucket on every
request. Enable it only behind a proxy known to *overwrite* the header.

---

## 10. Verifying any of this

```bash
npm run infra:up
npm run backend:test:integration
```

The failure-mode suite simulates each outage by pointing one client at a closed
port, so the client's own timeout, retry, and error-translation code runs
exactly as it would in production. To watch a real one instead, stop a single
container and exercise the affected endpoint — `/readyz` should name it, and
every row in §1 should hold.
