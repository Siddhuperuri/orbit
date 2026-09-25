# System architecture

Cross-cutting view of ORBIT: runtime topology, environments, testing, and
observability. Component-level detail lives in
[backend.md](backend.md), [frontend.md](frontend.md),
[data-flow.md](data-flow.md), [ai-pipeline.md](ai-pipeline.md), and
[security.md](security.md).

---

## 1. Shape

ORBIT is a **modular monolith** ([ADR-0001](../decisions/0001-modular-monolith.md)):
one Python package, one image, two processes, plus a Next.js frontend.

```
                    ┌───────────────────────────────┐
                    │   Reverse proxy / ingress     │   ← single origin
   browser ────────▶│   TLS termination             │     (ADR-0009)
                    │   /  → web     /api/* → api   │
                    └───────┬───────────────┬───────┘
                            │               │
                  ┌─────────▼──────┐  ┌─────▼──────────┐
                  │ web            │  │ api            │
                  │ Next.js (RSC   │  │ FastAPI        │
                  │ shell only)    │  │ uvicorn        │
                  └────────────────┘  └──┬──────────┬──┘
                                         │          │ enqueue
                            ┌────────────▼───┐  ┌───▼──────────┐
                            │ PostgreSQL 17  │  │ Redis        │
                            │ + pgvector     │  │ broker+cache │
                            │ source of truth│  └───┬──────────┘
                            └────────▲───────┘      │ consume
                                     │              │
                            ┌────────┴──────────────▼─────────┐
                            │ worker — Celery, prefork pool   │
                            │ parse → chunk → embed → index   │
                            └────────┬────────────────────────┘
                                     │
                            ┌────────▼────────┐      ┌──────────────┐
                            │ S3 / MinIO      │      │ AI provider  │
                            │ document bytes  │      │ (or fake)    │
                            └─────────────────┘      └──────────────┘
```

Two processes, split by **runtime characteristics** rather than business domain:

| Process | Work | Why separate |
|---|---|---|
| `api` | Bounded, fast, request-scoped | Must stay responsive |
| `worker` | Long, CPU-bound, untrusted input | Needs process isolation and hard kill limits ([ADR-0002](../decisions/0002-celery-prefork-job-queue.md)) |

They share one codebase and one database, so the invariant binding a document to
its chunks never crosses a network boundary.

## 2. Data ownership

| Store | Holds | Authority | If lost |
|---|---|---|---|
| PostgreSQL | Users, workspaces, documents, chunks, embeddings, jobs, conversations, audit | **Source of truth** | Catastrophic — restore from PITR backup |
| Object storage | Original uploaded bytes | Authoritative for file content only | Documents unreadable; metadata and search survive |
| Redis | Celery queue, cache | **Disposable** | In-flight jobs lost; committed data unaffected |

Redis is deliberately not authoritative for anything. Anything that must survive
a restart lives in PostgreSQL.

## 3. Environments

### 3.1 Local development

Infrastructure comes from Compose; application processes run on the host for
fast reload.

```
docker compose  ──  postgres (pgvector) : 5432    all bound to 127.0.0.1,
                    redis               : 6379    never 0.0.0.0
                    minio               : 9000/9001
host            ──  uvicorn --reload    : 8000
                    celery worker       : —
                    next dev            : 3000  ──  /api/* rewritten to :8000
```

The single-origin contract of ADR-0009 holds locally through a Next.js
`rewrites` proxy, so cookie behaviour in development matches production.

Deliberate properties:

- **No AI credentials required.** `ORBIT_AI_PROVIDER=fake` is the default and is
  a fully supported operating mode ([ADR-0007](../decisions/0007-ai-provider-ports.md)).
- **Compose refuses to start half-configured.** Required variables use
  `${VAR:?message}`, so a missing password fails immediately rather than booting
  with a default.
- **A separate `orbit_test` database** is provisioned at first boot, so a
  destructive test run cannot wipe local work.
- Extensions (`vector`, `pg_trgm`, `uuid-ossp`) are created by init scripts,
  because `CREATE EXTENSION` needs privileges the application role must not hold.

### 3.2 Production

```
            ingress / ALB  (TLS 1.2+, HSTS)
                   │
        ┌──────────┴───────────┐
        │                      │
   web × N                api × N ────────┐
   (stateless)            (stateless)     │
                               │          │
                          managed         │        worker × M
                          PostgreSQL      │        (stateless, prefork)
                          + pgvector      │             │
                          PITR backups    └──── managed Redis ──┘
                               │
                          S3 (versioned, SSE, private)
```

Every process is stateless and horizontally scalable. All state is in managed
services with their own backup and failover.

| Concern | Decision |
|---|---|
| Migrations | Run as a **pre-deploy job**, expand/contract only, never on app start — concurrent instances would race |
| Deploy | Rolling. `api` and `worker` deploy together at the same version (ADR-0001) |
| Schema compatibility | Every migration is backward-compatible with the previous release, because both run simultaneously during a rollout |
| Shutdown | `SIGTERM` → stop accepting, drain in-flight, exit. Worker finishes its current task or is redelivered |
| Secrets | Injected from a secret manager at runtime. Never in the image, never in the repository |
| Metrics port | Internal network only, never exposed by the ingress ([ADR-0015](../decisions/0015-observability-strategy.md)) |

**Scaling levers, in the order they will actually be needed:**

1. `worker` replicas — ingestion is the first thing to saturate, and it is
   embarrassingly parallel.
2. `api` replicas — cheap and stateless.
3. PostgreSQL read replicas for retrieval, once read load dominates.
4. Partitioning `chunks` by workspace, once the ANN index stops fitting in memory.

None of these are built now. They are named so the design does not preclude them.

## 4. Testing architecture

Four layers, each answering a different question.

| Layer | Location | Needs | Answers |
|---|---|---|---|
| **Unit** | `backend/tests/unit` | nothing | Is the business rule correct? |
| **Integration** | `backend/tests/integration` | Postgres, Redis, MinIO | Does persistence, retrieval, and the pipeline behave against real infrastructure? |
| **API** | `backend/tests/api` | as above | Does the HTTP contract — status, envelope, authorization — hold? |
| **E2E** | `e2e/` | full stack | Does the user journey work in a browser? |

Marked with pytest markers, so `npm run backend:test` runs the
infrastructure-free subset in seconds and CI runs everything.

**Principles that are enforced, not aspirational:**

- **The whole suite runs with no AI credentials and no network.** CI has no
  provider keys, which is what keeps this true.
- **Integration tests use real PostgreSQL.** pgvector distance operators, HNSW
  behaviour, full-text ranking, and transactional semantics cannot be honestly
  tested against SQLite. Each test runs inside a transaction that is rolled
  back, so tests are isolated and fast without recreating schema.
- **Every tenant-scoped resource has a cross-tenant denial test.** A resource
  without one is incomplete (ADR-0004).
- **Failure cases are mandatory.** Poison documents, oversized uploads, spoofed
  MIME types, provider outages, token reuse, concurrent updates.
- **Deterministic where it matters.** The fake AI provider makes retrieval and
  citation logic reproducible; retrieval *quality* is measured separately as a
  benchmark against a real provider, not asserted in tests.
- Tests are never deleted or skipped to make a build pass.

**Critical journeys covered end-to-end:** register → login → create workspace →
upload → process → search → ask → inspect citation.

## 5. Observability architecture

Detailed in [ADR-0015](../decisions/0015-observability-strategy.md).

The defining constraint is that the interesting failures cross a process
boundary asynchronously: the HTTP request that started a document returned 202
and finished long before the worker failed on attempt three.

```
request ──▶ middleware assigns request_id (ULID), binds to contextvar
        ──▶ every log record carries it automatically
        ──▶ id is embedded in the Celery task payload
        ──▶ worker restores it into its logging context
        ──▶ every retry logs under the same id
```

One document's entire lifecycle is retrievable with a single query.

| Signal | Mechanism |
|---|---|
| Logs | `structlog`; JSON deployed, console locally. Structural redaction of sensitive keys — a processor, not developer diligence |
| Metrics | `prometheus-client` on a **separate internal port**. Route templates as labels; never `user_id`, `workspace_id`, or raw paths — cardinality is a hard rule |
| Errors | `ErrorReporter` port, no-op by default, Sentry adapter later |
| Liveness | `/healthz` — checks **nothing external**. A liveness probe that checks the database turns a database blip into a self-inflicted restart storm |
| Readiness | `/readyz` — checks DB, Redis, storage with short timeouts; reports per-dependency status |

Distributed tracing is deferred with a documented migration path: correlation
IDs follow W3C `traceparent` shape and instrumentation lives in middleware and
decorators, so adopting OpenTelemetry later changes the emitter, not the call
sites.

## 6. Failure behaviour

Summarised here; the full per-dependency behaviour, with runbook steps and the
tests that verify each claim, is
[docs/operations/failure-modes.md](../operations/failure-modes.md).

| Failure | Behaviour | Why |
|---|---|---|
| AI provider down | Chat returns 503 + `Retry-After`. **Search still works**, lexically, and says so (`degraded`). Processing retries with backoff, document stays `PENDING` | The document is fine; the provider is not. Marking it `FAILED` would be a lie |
| Object storage down | Uploads fail fast. Metadata browsing and search still work | Degraded, not dead |
| Redis down | Uploads **accepted**. Rate limiting and the query cache fail open; the committed job is re-published by recovery | Nothing authoritative lives in Redis. Refusing the upload would report a failure that did not happen ([ADR-0024](../decisions/0024-redis-usage-and-degradation.md)) |
| PostgreSQL down | `/readyz` fails, instance leaves rotation | Nothing works without the source of truth; fail visibly |
| Worker killed mid-job | Message redelivered; tasks are idempotent | Prefork means SIGKILL after partial work is expected |
| Poison document | Soft limit records `FAILED` with a reason; hard limit SIGKILLs the child | One document, one child process |
| Deploy rollback | Previous image redeployed; schema is backward-compatible | Expand/contract makes rollback safe |

## 7. Extension points

The brief requires images, web pages, and further formats without a rewrite. The
seams already exist:

| Extension | Seam |
|---|---|
| New document format | `DocumentParser` port ([ADR-0012](../decisions/0012-document-parser-architecture.md)). Chunking onward is format-agnostic |
| New AI provider | `EmbeddingProvider` / `LLMProvider` ports (ADR-0007). Note: changing embedding model is a **data migration**, not config |
| Reranking | `Reranker` port, no-op default (ADR-0005) |
| Dedicated search engine | `SearchRepository` port — replacement, not rewrite |
| Presigned direct upload | `ObjectStorage` port ([ADR-0011](../decisions/0011-object-storage-and-upload.md)) |
| Distributed tracing | Middleware/decorator instrumentation (ADR-0015) |

## 8. What this architecture deliberately does not do

Stated so that their absence is understood as a decision rather than an
oversight:

- **No microservices.** No demonstrated need; real cost to the document→chunk invariant.
- **No separate search cluster.** PostgreSQL is already the source of truth and already required.
- **No event sourcing or CQRS read store.** Read/write model separation is at the repository boundary only ([ADR-0010](../decisions/0010-database-access-pattern.md)).
- **No Kubernetes assumption.** Stateless containers run anywhere; nothing depends on a specific orchestrator.
- **No multi-region.** Single region until there is a latency or residency requirement.
- **No general-purpose cache.** The only cached value is the query embedding — a pure function of text, keyed by workspace and embedding space. Authorization, result pages, and document lists are never cached ([ADR-0024](../decisions/0024-redis-usage-and-degradation.md)).
