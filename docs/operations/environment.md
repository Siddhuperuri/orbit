# Environment configuration

Every environment-specific value enters ORBIT through
[`backend/src/orbit/core/config.py`](../../backend/src/orbit/core/config.py) and
nowhere else. No module reads `os.environ` directly, so the complete set of
things that can vary between environments is the table below.

**Configuration is validated at startup and failures are fatal.** A process that
refuses to boot is far cheaper to diagnose than one that boots and behaves
subtly wrongly.

## Conventions

- Variables read by the application are prefixed **`ORBIT_`**.
- Unprefixed variables (`POSTGRES_*`, `MINIO_*`) configure third-party
  containers and are consumed only by `docker-compose.yml`.
- `NEXT_PUBLIC_*` values are **inlined into the browser bundle**. Nothing secret
  may ever use that prefix.
- Unknown `ORBIT_*` variables are ignored rather than fatal, so a rolling deploy
  carrying variables for an adjacent release can still boot and roll back.

## Runtime

| Variable | Default | Notes |
|---|---|---|
| `ORBIT_ENV` | `development` | `development` \| `test` \| `production`. Production enables extra validation and disables interactive API docs. |
| `ORBIT_LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |
| `ORBIT_LOG_FORMAT` | `console` | `console` \| `json`. **Must be `json` in production** — enforced at startup. |
| `ORBIT_SERVICE_NAME` | `orbit-api` | Appears on every log record. The worker sets `orbit-worker`. |

## PostgreSQL

| Variable | Default | Notes |
|---|---|---|
| `ORBIT_DATABASE_URL` | — | **Required.** Must use the `postgresql+asyncpg://` scheme. |
| `ORBIT_TEST_DATABASE_URL` | — | Integration-test database. Dropped and recreated freely, so it must never point at real data. |
| `ORBIT_DB_POOL_SIZE` | `10` | 1–100. |
| `ORBIT_DB_MAX_OVERFLOW` | `5` | 0–100. Connections beyond the pool under burst. |
| `ORBIT_DB_POOL_TIMEOUT_SECONDS` | `10` | How long a request waits for a connection before failing. |
| `ORBIT_DB_STATEMENT_TIMEOUT_SECONDS` | `15` | 1–600. Server-side ceiling on one statement, on every API connection. A client-side deadline stops the *waiting*, not the query: only PostgreSQL cancelling it returns the connection and its locks. |
| `ORBIT_WORKER_DB_STATEMENT_TIMEOUT_SECONDS` | `120` | 1–3600. The worker's equivalent. Far higher because re-index batches and recovery sweeps legitimately run long; the API's limit would cancel ordinary maintenance. |
| `ORBIT_DB_ECHO` | `false` | Logs every statement **with bound parameters**, so it puts user data in logs. **Rejected in production.** |

Pool sizing is per process. Total connections are
`replicas × (pool_size + max_overflow)`, and that number must stay below the
server's `max_connections`.

## Redis

| Variable | Default | Notes |
|---|---|---|
| `ORBIT_REDIS_URL` | — | **Required.** Application cache. |
| `ORBIT_CELERY_BROKER_URL` | — | **Required.** Separate database index from the cache. |
| `ORBIT_CELERY_RESULT_BACKEND` | — | **Required.** Operational inspection only; job state lives in PostgreSQL. |
| `ORBIT_REDIS_CONNECT_TIMEOUT_SECONDS` | `2` | 0–30. |
| `ORBIT_REDIS_COMMAND_TIMEOUT_SECONDS` | `2` | 0–30. Short on purpose: every command is a single O(1) operation, so one that has not answered in two seconds is broken, not slow. |
| `ORBIT_QUERY_VECTOR_CACHE_ENABLED` | `true` | The only cache in ORBIT. Holds query embeddings, keyed by workspace, embedding space, and the hash of the query text. |
| `ORBIT_QUERY_VECTOR_CACHE_TTL_SECONDS` | `3600` | 10 s–24 h. A model change invalidates entries regardless, since the space is part of the key. |

Three separate database indexes so that flushing the cache cannot destroy the
queue.

**Nothing authoritative lives in Redis, and nothing authorization-dependent is
cached.** Losing Redis makes search slower, rate limiting absent, and document
processing delayed — it takes nothing down. The full behaviour is
[ADR-0024](../decisions/0024-redis-usage-and-degradation.md) and
[failure-modes.md](failure-modes.md).

## Object storage

| Variable | Default | Notes |
|---|---|---|
| `ORBIT_S3_ENDPOINT_URL` | unset | MinIO's URL locally. **Leave empty for real AWS S3.** |
| `ORBIT_S3_REGION` | `us-east-1` | |
| `ORBIT_S3_BUCKET` | — | **Required.** |
| `ORBIT_S3_ACCESS_KEY_ID` | — | **Required.** |
| `ORBIT_S3_SECRET_ACCESS_KEY` | — | **Required.** |
| `ORBIT_S3_FORCE_PATH_STYLE` | `true` | MinIO needs path-style; real S3 uses virtual-host style. |
| `ORBIT_S3_CONNECT_TIMEOUT_SECONDS` | `3` | 0–60. |
| `ORBIT_S3_READ_TIMEOUT_SECONDS` | `10` | 0–300. Per socket read, not per operation — a large multipart upload is many reads, so a healthy 50 MB upload is not capped at 10 s. |
| `ORBIT_S3_MAX_ATTEMPTS` | `2` | 1–10. Botocore's internal retries. Small on purpose: the caller's policy owns retrying. |

Credentials come from configuration rather than the ambient AWS credential
chain, so a developer's personal AWS profile can never be picked up by accident
and used against a real bucket.

## Security

| Variable | Default | Notes |
|---|---|---|
| `ORBIT_SECRET_KEY` | — | **Required, ≥32 characters.** Signs access tokens. Rotating it invalidates every outstanding token — the intended emergency lever. |
| `ORBIT_ACCESS_TOKEN_TTL_SECONDS` | `900` | 60–3600. Short TTL is what bounds the damage of a leaked token. |
| `ORBIT_REFRESH_TOKEN_TTL_SECONDS` | `2592000` | ≥3600. Session lifetime. |
| `ORBIT_CORS_ALLOWED_ORIGINS` | empty | **Comma-separated exact origins.** `*` is rejected. **Must be empty in production.** |

Generate the key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

`ORBIT_CORS_ALLOWED_ORIGINS` exists for development only. In production ORBIT is
served from a single origin ([ADR-0009](../decisions/0009-single-origin-cookie-transport.md)),
so nothing is cross-origin and a populated allow-list means a misconfigured
deploy — which startup validation rejects.

## Brute-force protection

Two independent limits guard every credential check, and either one tripping
rejects the request with `429` and a `Retry-After` header. Neither is
sufficient alone: a per-IP limit is defeated by a botnet, a per-account limit
by rotating targets. Together they force an attacker to be both distributed
*and* slow. See [ADR-0017](../decisions/0017-rate-limiting.md).

| Variable | Default | Notes |
|---|---|---|
| `ORBIT_LOGIN_RATE_LIMIT_PER_ACCOUNT` | `10` | Attempts against one account per window. |
| `ORBIT_LOGIN_RATE_LIMIT_PER_IP` | `50` | Attempts from one client address per window, across all accounts. |
| `ORBIT_LOGIN_RATE_LIMIT_WINDOW_SECONDS` | `900` | Fixed window, 10 s–24 h. |
| `ORBIT_ACCOUNT_EMAIL_RATE_LIMIT_PER_ACCOUNT` | `3` | Reset requests and verification resends per account. |
| `ORBIT_ACCOUNT_EMAIL_RATE_LIMIT_PER_IP` | `10` | Same, per client address. |
| `ORBIT_ACCOUNT_EMAIL_RATE_LIMIT_WINDOW_SECONDS` | `3600` | 60 s–24 h. |
| `ORBIT_UPLOAD_RATE_LIMIT_PER_ACCOUNT` | `60` | Uploads (create or new version) per account per window. Independent of the per-request `ORBIT_MAX_UPLOAD_BYTES` ceiling — this bounds *rate*, that bounds *size*. |
| `ORBIT_UPLOAD_RATE_LIMIT_PER_IP` | `120` | Same, per client address. |
| `ORBIT_UPLOAD_RATE_LIMIT_WINDOW_SECONDS` | `3600` | 10 s–24 h. |
| `ORBIT_SEARCH_RATE_LIMIT_PER_ACCOUNT` | `120` | Searches per user per window. Each costs a paid embedding call plus a full-text scan and an HNSW probe. |
| `ORBIT_SEARCH_RATE_LIMIT_PER_IP` | `300` | Same, per client address. |
| `ORBIT_SEARCH_RATE_LIMIT_WINDOW_SECONDS` | `300` | 10 s–24 h. A separate budget from chat: refining a query is a burst, asking questions is not. |
| `ORBIT_TRUST_PROXY_HEADERS` | `false` | Whether `X-Forwarded-For` is believed. **The default is the control** — see below. |

Counters live in Redis. **If Redis is unreachable the limiter fails open** and
logs `ratelimit.backend_unavailable` at ERROR. That is deliberate: failing
closed would turn a Redis outage into a total authentication outage, including
for the operators trying to fix it. Argon2id still makes each attempt
expensive, and `/readyz` already reports Redis down, so the degraded window is
bounded and visible rather than silent.

The account limit is cleared on a successful login; the per-IP limit is not.

### Client address

The per-IP limit is only as good as the address it keys on, and
`X-Forwarded-For` is client-supplied. With `ORBIT_TRUST_PROXY_HEADERS=false`
(the default) the header is ignored and the socket peer address is used.

Enable it **only** where a reverse proxy is known to *overwrite* the header
rather than append to it. If it is enabled and the API is reachable directly,
an attacker rotates the header and gets an unlimited supply of fresh rate-limit
buckets, and can write arbitrary addresses into audit records.

Behind an unconfigured proxy with the flag off, every client collapses into the
proxy's own address — wrong, but wrong in the safe direction, and visible the
first time a limit trips for everyone at once.
One valid account must not be a way to launder guesses against every other
account from the same host.

## Email

There is **no `fake` email provider**, and that absence is the design. A sender
that accepts a message and does nothing makes a broken password-reset flow look
healthy in every environment where nobody checks an inbox — and the environment
where someone finally does is production, during a lockout.

| Variable | Default | Notes |
|---|---|---|
| `ORBIT_EMAIL_PROVIDER` | `unconfigured` | `unconfigured` \| `console` \| `smtp`. |
| `ORBIT_EMAIL_FROM_ADDRESS` | `no-reply@orbit.local` | Sender address. |
| `ORBIT_PASSWORD_RESET_URL_TEMPLATE` | localhost | Must contain `{token}`; https required in production. |
| `ORBIT_EMAIL_VERIFICATION_URL_TEMPLATE` | localhost | Same. |

- `unconfigured` — every send raises `EmailDeliveryError`. Any flow depending
  on real delivery fails visibly the first time it is exercised.
- `smtp` — delivers through any SMTP relay (SES, Postmark, Mailgun, Gmail, a company server).
  Requires `ORBIT_SMTP_HOST`; the rest are below. Gmail needs an **app password**
  (Google account → Security → 2-Step Verification → App passwords), not the account password:
  `ORBIT_SMTP_HOST=smtp.gmail.com`, `ORBIT_SMTP_PORT=587`, `ORBIT_SMTP_USERNAME=<address>`,
  `ORBIT_SMTP_PASSWORD=<app password>`, and set `ORBIT_EMAIL_FROM_ADDRESS` to the same address.
- `console` — writes the message to the log so a developer can copy the link.
  **Rejected at startup in production**: a one-time account link in a log
  stream is a credential in a log stream, readable by anyone with log access.

| Variable | Default | Notes |
|---|---|---|
| `ORBIT_SMTP_HOST` | — | Required for `smtp`. |
| `ORBIT_SMTP_PORT` | `587` | 587 = STARTTLS; 465 = implicit TLS (`ORBIT_SMTP_USE_SSL`). |
| `ORBIT_SMTP_USERNAME` / `ORBIT_SMTP_PASSWORD` | — | Set together, or both omitted for an open relay. |
| `ORBIT_SMTP_STARTTLS` / `ORBIT_SMTP_USE_SSL` | `true` / `false` | Mutually exclusive; production requires one. |
| `ORBIT_SMTP_TIMEOUT_SECONDS` | `15` | 1–120. |

Link templates point at the **frontend**, which posts the token back to the
API. The token therefore never appears in an API URL that a reverse proxy,
access log, or `Referer` header would record.

## Uploads

Supported formats: PDF, Markdown, plain text — determined by inspecting bytes,
never by the declared extension or `Content-Type` (ADR-0011). See
`ORBIT_UPLOAD_RATE_LIMIT_*` above for the rate limit alongside this size limit.

| Variable | Default | Notes |
|---|---|---|
| `ORBIT_MAX_UPLOAD_BYTES` | `52428800` (50 MiB) | Enforced by counting bytes **as they arrive**, not after the fact. `Content-Length` is only a cheap pre-flight rejection; the live counter is the actual control. |

Upload endpoints accept a **raw request body**, not `multipart/form-data` —
`POST /workspaces/{id}/documents?filename=...` with the file's bytes as the
entire body. This is deliberate: FastAPI's conventional `File(...)` upload is
backed by Starlette's multipart parser, which fully buffers the body before a
route handler runs, which would make the live size check above moot. See
ADR-0011's implementation notes for the full reasoning.

## Document processing

Semantics, alerting, and runbook: [worker.md](worker.md).

| Variable | Default | Notes |
|---|---|---|
| `ORBIT_PROCESSING_MAX_ATTEMPTS` | `5` | Attempts per processing run. Only transient failures consume retries. |
| `ORBIT_PROCESSING_RETRY_BASE_SECONDS` | `15` | First retry delay ceiling; doubles per attempt, equal jitter. Must not exceed the max. |
| `ORBIT_PROCESSING_RETRY_MAX_SECONDS` | `900` | Cap on any single retry delay. |
| `ORBIT_PROCESSING_LEASE_SECONDS` | `960` | A running job not renewed for this long is presumed abandoned. **Production requires it to exceed the hard time limit.** |
| `ORBIT_PROCESSING_REDELIVERY_GRACE_SECONDS` | `600` | A queued job whose message was last published longer ago is re-published. |
| `ORBIT_PROCESSING_RECOVERY_INTERVAL_SECONDS` | `60` | Beat schedule for the recovery sweep. |
| `ORBIT_PROCESSING_TASK_TIME_LIMIT_SECONDS` | `900` | Hard limit (SIGKILL). |
| `ORBIT_PROCESSING_TASK_SOFT_TIME_LIMIT_SECONDS` | `840` | Must be below the hard limit, or a timeout cannot be recorded. |
| `ORBIT_PROCESSING_MAX_PAGES` | `2000` | PDF page ceiling. |
| `ORBIT_PROCESSING_MAX_CHARACTERS` | `5000000` | Extracted-text ceiling, checked as text accumulates. |
| `ORBIT_PROCESSING_MAX_CHUNKS` | `20000` | Chunk ceiling, bounding embedding cost per document. |
| `ORBIT_WORKER_MAX_MEMORY_PER_CHILD_KB` | `512000` | Worker child recycled after crossing it. |

## AI providers

| Variable | Default | Notes |
|---|---|---|
| `ORBIT_AI_PROVIDER` | `fake` | `fake` \| `openai`. |
| `ORBIT_OPENAI_API_KEY` | unset | **Required when the provider is `openai`.** Held as a secret type; never appears in a repr or settings dump. |
| `ORBIT_OPENAI_BASE_URL` | unset | **Required when the provider is `openai`.** Any OpenAI-compatible endpoint; `https://` in production. |
| `ORBIT_EMBEDDING_MODEL` | unset | **Required when the provider is `openai`.** Ignored by `fake`, which records its own id. |
| `ORBIT_EMBEDDING_DIMENSIONS` | **required** | Must match the pgvector column width (1536). 1–2000 (HNSW's limit for `vector`). |
| `ORBIT_EMBEDDING_BATCH_SIZE` | `96` | Most inputs per provider request. |
| `ORBIT_EMBEDDING_MAX_BATCH_TOKENS` | `100000` | Most (approximate) tokens per request. |
| `ORBIT_EMBEDDING_REQUEST_TIMEOUT_SECONDS` | `60` | Per request. |
| `ORBIT_EMBEDDING_MAX_RETRIES` | `3` | In-process retries of one request before the job's durable backoff takes over. |
| `ORBIT_EMBEDDING_RETRY_BASE_SECONDS` | `1` | First in-process retry delay ceiling; doubles, equal jitter. |
| `ORBIT_EMBEDDING_RETRY_MAX_WAIT_SECONDS` | `20` | A longer `Retry-After` is not waited on in-process. |
| `ORBIT_EMBEDDING_REINDEX_BATCH_SIZE` | `500` | Chunks per re-index transaction. |
| `ORBIT_VECTOR_SEARCH_EF_SEARCH` | `100` | HNSW candidate list; tuned in `docs/benchmarks/vector-search.md`. |
| `ORBIT_SEARCH_CANDIDATES_PER_RETRIEVER` | `50` | Candidates each retriever contributes before fusion (10–200). |
| `ORBIT_SEARCH_LEXICAL_MATCH` | `any` | `any` (OR) or `all` (AND) over query terms. `all` halved MRR in evaluation. |
| `ORBIT_LLM_MODEL` | `gpt-4o-mini` | Chat model for answers. Ignored by `fake` (`orbit-fake-llm-v1`). Must not be blank. |
| `ORBIT_LLM_CONTEXT_WINDOW` | `128000` | The model's window; the context budget is computed from it. |
| `ORBIT_LLM_REASONING_EFFORT` | unset | `none` \| `minimal` \| `low` \| `medium` \| `high`, sent as `reasoning_effort`, for reasoning models only (unset sends nothing; gpt-4o-mini rejects it). A reasoning model counts its thinking against `ORBIT_ANSWER_MAX_TOKENS`, so at its default level it can spend the whole budget thinking and truncate the answer -- Gemini does; use `low`. |
| `ORBIT_LLM_REQUEST_TIMEOUT_SECONDS` | `30` | Per request, and between streamed chunks. |
| `ORBIT_LLM_MAX_RETRIES` | `2` | In-process retries; a stream is retried only before its first token. |
| `ORBIT_LLM_RETRY_BASE_SECONDS` | `0.5` | First retry delay ceiling; doubles, equal jitter. |
| `ORBIT_LLM_RETRY_MAX_WAIT_SECONDS` | `4` | A longer `Retry-After` is returned to the user as `GENERATION_RATE_LIMITED`. |
| `ORBIT_LLM_CIRCUIT_FAILURE_THRESHOLD` | `5` | Consecutive provider failures that open the circuit (rate limits excluded). |
| `ORBIT_LLM_CIRCUIT_RESET_SECONDS` | `30` | Open time before one probe is allowed. |
| `ORBIT_ANSWER_TEMPERATURE` | `0.1` | 0–2. |
| `ORBIT_ANSWER_MAX_TOKENS` | `800` | Longest answer; reserved out of the window. A truncated answer is `partial` / `max_tokens`. |
| `ORBIT_ANSWER_MAX_CONTEXT_TOKENS` | `6000` | Ceiling on retrieved text per prompt. With `ORBIT_ANSWER_MAX_TOKENS` it must stay below the window. |
| `ORBIT_ANSWER_RETRIEVAL_TOP_K` | `12` | Fused candidates retrieved per question. |
| `ORBIT_ANSWER_RERANK_TOP_K` | `8` | Kept after reranking; at most `RETRIEVAL_TOP_K`. |
| `ORBIT_ANSWER_MAX_SOURCES` | `8` | Passages that may enter the context. |
| `ORBIT_ANSWER_HISTORY_MESSAGES` | `6` | Earlier messages shown to the model (citation markers stripped). |
| `ORBIT_ANSWER_MAX_HISTORY_TOKENS` | `1500` | Oldest history is dropped first. |
| `ORBIT_ANSWER_GENERATION_TIMEOUT_SECONDS` | `60` | Wall clock for one answer, retries included. |
| `ORBIT_CHAT_RATE_LIMIT_PER_ACCOUNT` | `30` | Questions per user per window. |
| `ORBIT_CHAT_RATE_LIMIT_PER_IP` | `120` | Questions per client address per window. |
| `ORBIT_CHAT_RATE_LIMIT_WINDOW_SECONDS` | `300` | |

`fake` is a deterministic, offline provider and a **fully supported operating
mode**, not a test stub. The whole application runs under it with no credentials
and no network.

> **`ORBIT_EMBEDDING_DIMENSIONS` is schema, not configuration.** The pgvector
> column dimension is fixed by migration. Changing this value requires
> re-embedding the corpus and rebuilding the HNSW index — see
> [docs/database/embeddings.md](../database/embeddings.md). It is not a knob.
> Changing `ORBIT_EMBEDDING_MODEL` at the same width is a re-index, not a
> migration: `npm run worker:index-status`, then `npm run worker:reindex`.

### Using Google Gemini

`openai` names the HTTP API, not the vendor. Google serves the same API for
Gemini at `https://generativelanguage.googleapis.com/v1beta/openai`, so Gemini
is configuration only -- see the commented block in `.env.example`. Measured
against it (2026-09):

- `gemini-embedding-001` honours `dimensions: 1536`, so the schema is unchanged.
  It omits `index` on the first item of a batch (protobuf drops zero values);
  the adapter reads a missing index as 0.
- Chat models are reasoning models; set `ORBIT_LLM_REASONING_EFFORT=low`, or
  answers are cut short. `gemini-2.5-flash` is closed to new keys.
- Errors arrive as a one-element JSON list, and the retry delay is in the body
  (`google.rpc.RetryInfo`), not a `Retry-After` header; both are read.
- The free tier allows **1,000 embedded texts per day per model**
  (`EmbedContentRequestsPerDayPerUserPerProjectPerModel-FreeTier`), and every
  text counts, including those in requests Google rejects. A 1,209-chunk corpus
  therefore cannot be embedded in one day if retries are wasted: keep batches
  small (40), retry little (2), and expect a re-index that hits the limit to stop
  with `stale_chunks` remaining. It resumes where it stopped (`npm run
  worker:reindex`) after the daily reset (midnight Pacific), or finish at once by
  enabling billing on the Google project, which has no daily cap.

## Observability

| Variable | Default | Notes |
|---|---|---|
| `ORBIT_METRICS_ENABLED` | `true` | |
| `ORBIT_METRICS_HOST` | `127.0.0.1` | Bind address for both metrics ports. `0.0.0.0` only inside a container. |
| `ORBIT_METRICS_PORT` | `9100` | API metrics port. **Must never be exposed by the ingress.** |
| `ORBIT_WORKER_METRICS_PORT` | `9101` | Worker metrics port (same rule). |
| `ORBIT_METRICS_REFRESH_INTERVAL_SECONDS` | `15` | How often queue and pool gauges are recomputed (5-300). |
| `ORBIT_DB_SLOW_QUERY_THRESHOLD_MS` | `500` | Statements slower than this log `db.slow_query`. |
| `ORBIT_SLOW_REQUEST_THRESHOLD_MS` | `2000` | Requests slower than this log at WARNING with `slow=true`. |
| `PROMETHEUS_MULTIPROC_DIR` | unset | Not an `ORBIT_` setting: read by `prometheus_client` at import. Set to an empty directory to aggregate multiple processes; required for a prefork worker to serve metrics. |

## Frontend

| Variable | Default | Notes |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `/api` | **A relative path, never an origin.** An absolute URL breaks the `HttpOnly` auth cookies. |
| `ORBIT_DEV_API_PROXY_TARGET` | `http://localhost:8000` | Where the dev-server rewrite sends `/api/*`. Never reaches the browser, which is why it is not `NEXT_PUBLIC_`. |

## Third-party container variables

Consumed by `docker-compose.yml` only. Compose uses `${VAR:?message}` for these,
so a half-configured environment fails immediately rather than booting with
defaults.

| Variable | Notes |
|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Password is required. |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | Both required. |

## What startup rejects

| Condition | Why |
|---|---|
| `ORBIT_SECRET_KEY` shorter than 32 characters | Below the HS256 key length the JWA spec requires. |
| The `.env.example` placeholder key in production | It is public. |
| `*` in `ORBIT_CORS_ALLOWED_ORIGINS` | Browsers reject a wildcard on credentialed requests, so auth would silently break. |
| Any CORS origin in production | Single-origin deployment; a value means a misconfiguration. |
| `ORBIT_AI_PROVIDER=openai` without a key | Every AI call would fail at request time instead. |
| `ORBIT_DB_ECHO=true` in production | Statement logs carry bound parameters, i.e. user data. |
| `ORBIT_LOG_FORMAT=console` in production | Logs must be machine-readable. |
| `ORBIT_EMAIL_PROVIDER=console` in production | It writes one-time account links to the log stream. |
| A link template without `{token}` | The mailed link could never work — caught now, not when a locked-out user needs it. |
| An `http://` link template in production | A one-time token in a plaintext URL is a token on the wire. |
| Out-of-range pool sizes, TTLs, or upload limits | Nonsensical values fail fast rather than at load. |

## Secret handling

- `.env` is gitignored. **CI fails the build if a dotenv file becomes tracked**
  and scans full branch history for credentials.
- Production secrets come from a secret manager at runtime, never baked into an
  image. `.dockerignore` excludes every dotenv file from the build context.
- Sensitive values are redacted from logs structurally, by a processor that
  drops known-sensitive keys at any nesting depth — not by developer diligence.
