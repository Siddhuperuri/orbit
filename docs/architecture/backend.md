# Backend architecture

Layering, API contract, persistence, and object storage. System-level topology
is in [system.md](system.md); flows are in [data-flow.md](data-flow.md).

---

## 1. Layers

```
composition ──▶ api ──▶ ┌ application ┐ ──▶ domain ──▶ core
                        └ infrastructure ┘
```

| Layer | Contains | May import |
|---|---|---|
| `core` | Settings, logging setup, security primitives, clock | — |
| `domain` | Entities, value objects, pure rules, **ports**, error types | `core` |
| `application` | Use cases, transaction boundaries, orchestration | `domain`, `core` |
| `infrastructure` | Adapters: PostgreSQL, S3, Redis, AI providers, parsers | `domain`, `core` |
| `api` | Routing, request/response schemas, middleware, error mapping | `application`, `domain`, `core` |
| `composition` | Wiring — binds ports to adapters, builds the app | everything |

Enforced by `backend/.importlinter` in CI. Two rules carry the weight:

1. **`application` and `infrastructure` cannot import each other.** Use cases
   depend on ports in `domain`; adapters implement them; neither knows the other.
2. **`api` cannot import `infrastructure`.** "No database queries in route
   handlers" is structurally impossible, not a review convention.

### Directory layout

```
src/orbit/
  core/          config.py  logging.py  security.py  clock.py
  domain/
    models/      user.py  workspace.py  document.py  chunk.py  conversation.py
    ports/       storage.py  ai.py  parsing.py  queue.py  repositories.py
    errors.py
  application/
    auth/  workspaces/  documents/  search/  chat/
  infrastructure/
    db/          session.py  models/  repositories/  unit_of_work.py
    storage/     s3.py
    queue/       celery_app.py  tasks/
    ai/          base.py  fake.py  openai.py  reranker.py
    parsing/     registry.py  pdf.py  markdown.py  plaintext.py  normalize.py
    chunking/    chunker.py
  api/
    middleware/  request_id.py  logging.py  errors.py  security_headers.py
    v1/routers/  auth.py  workspaces.py  documents.py  folders.py  tags.py
                 search.py  chat.py  health.py
    v1/schemas/  one module per resource
  composition/   container.py  app.py  worker.py
```

Features are vertical slices inside `application`, not a flat pile of services.

## 2. Request path

```
HTTP
 └─ middleware: request_id → log context → security headers → error envelope
     └─ router: validate (Pydantic), resolve identity, no logic
         └─ use case: authorize, orchestrate, own the transaction
             └─ repository / port: execute, always with AccessContext
                 └─ response schema: explicit DTO, never an ORM model
```

Route handlers translate HTTP to a use-case call and back. Nothing else.

**Dependency injection** is constructor-based. `composition` builds a container
at startup and binds it to app state; FastAPI dependencies resolve use cases
from it. `api` never constructs an adapter, and a test swaps any port by
building a container with a different binding — no monkeypatching.

## 3. API architecture

### Conventions

| Aspect | Decision |
|---|---|
| Versioning | `/api/v1` in the path. Additive changes are non-breaking; removals or semantic changes require `v2` |
| Naming | Plural resource nouns, kebab-free, lowercase: `/api/v1/workspaces/{id}/documents` |
| Contracts | Dedicated Pydantic request/response schemas. **ORM models are never serialised** |
| Success | `200` read, `201` + `Location` create, `202` accepted-for-processing, `204` delete |
| Errors | Single envelope ([ADR-0014](../decisions/0014-error-handling-strategy.md)) |
| Pagination | **Keyset cursors**, never offset |
| Time | RFC 3339, UTC, always timezone-aware (ruff `DTZ` enforces this) |
| IDs | UUIDv7 — time-ordered, so they index well and sort meaningfully |

### Pagination

Offset pagination is rejected ([ADR-0010](../decisions/0010-database-access-pattern.md)):
`OFFSET n` scans and discards `n` rows, and results shift under concurrent
inserts, so a user paging through documents sees duplicates and gaps.

```json
{ "items": [ … ], "next_cursor": "eyJjIjoiMjAyNi0wOS0wOVQxMjowMDowMFoiLCJpIjoiMDE4ZiJ9" }
```

Cursors are opaque and **signed**, so a client cannot craft one to bypass
ordering or filtering. `limit` has a server-enforced maximum; no endpoint can
return an unbounded collection.

Lists are wrapped because they carry pagination metadata. Single resources are
returned bare — a `{"data": …}` wrapper on every response is noise.

### Filtering, sorting, search

Allowed filter fields, operators, and sort keys are **enumerated per endpoint**
as typed query models. An arbitrary filter DSL becomes an injection surface and
an unindexed-query generator; enumeration keeps every supported query provably
backed by an index.

### Long-running work

Upload returns `202` with the document resource in `PENDING`. The client polls
`GET /documents/{id}`; status transitions are the contract
([data-flow.md](data-flow.md)). Chat streams over SSE.

### Documentation

OpenAPI is generated from the application and served at `/docs` in
non-production. It is also the **source of frontend types**
([ADR-0016](../decisions/0016-frontend-data-fetching.md)), so a contract change
that breaks the client fails CI.

## 4. Database architecture

PostgreSQL 17 with `pgvector`, `pg_trgm`, `uuid-ossp`.

### Access pattern

Detailed in [ADR-0010](../decisions/0010-database-access-pattern.md):

- **Repositories** return domain entities on the write path and purpose-built
  **read models** on the query path — reads do not reconstruct aggregates.
- **The use case owns the transaction** through an explicit unit of work.
  Repositories never commit.
- **`lazy="raise"` on every relationship.** An unloaded relationship access
  raises instead of silently emitting a query, converting a latent N+1 into a
  deterministic test failure. Loading is always explicit at the query site.
- **Two session factories** — `AsyncSession` for the API, `Session` for the
  Celery worker (ADR-0002 requires a synchronous worker).
- **Every list query is bounded** by a keyset cursor and a maximum limit.

### Schema principles

| Concern | Rule |
|---|---|
| Keys | UUIDv7 primary keys; foreign keys always constrained |
| Tenancy | Every tenant-scoped table carries `workspace_id`, indexed, and it is in the `WHERE` clause of every query ([ADR-0004](../decisions/0004-authorization-in-repositories.md)) |
| Timestamps | `created_at`, `updated_at`, timezone-aware, database-defaulted |
| Deletion | **Soft delete** for user-facing entities (documents, folders, workspaces) so an accidental delete is recoverable. **Hard cascade** for derived data — chunks and embeddings are rebuildable and worthless without their document |
| Uniqueness | Enforced by constraint, not by check-then-insert. `(workspace_id, content_sha256)` for deduplication; `lower(email)` for accounts |
| Enums | Native PostgreSQL enums for closed sets like document status — ambiguous states are unrepresentable |
| Concurrency | Optimistic `version` column on entities that support concurrent edit; `SELECT … FOR UPDATE` where a job must be claimed exactly once |
| Migrations | Alembic, **expand/contract only**. Backward-compatible with the previous release, because both run during a rolling deploy |

### Indexes

Every non-trivial index is justified by a named query and recorded in
`docs/database/indexes.md`. Blind indexing costs write throughput and memory.

| Index | Serves |
|---|---|
| `documents (workspace_id, created_at DESC, id)` | Keyset-paginated document list — the most frequent query in the product |
| `documents (workspace_id, status)` partial on non-terminal states | The status poll; partial because `READY` rows dominate and are not polled |
| `documents (workspace_id, content_sha256)` unique | Deduplication, enforced not checked |
| `chunks USING hnsw (embedding vector_cosine_ops)` | Dense retrieval ([ADR-0005](../decisions/0005-hybrid-retrieval-rrf.md)) |
| `chunks USING gin (search_vector)` | Lexical retrieval |
| `chunks (document_id, ordinal)` | Ordered chunk read, and cascade delete |
| `refresh_tokens (token_hash)` unique | Refresh lookup and reuse detection |

**The known risk** is the interaction between the HNSW index and the mandatory
`workspace_id` filter: a selective pre-filter can cause an ANN scan to
under-return. pgvector's iterative index scan is the mitigation, and
`hnsw.ef_search` is tuned against **measured recall**, recorded in
`docs/benchmarks/retrieval.md`, rather than guessed. This is the single most
important measurement in the system.

### The embedding dimension is schema, not configuration

The `vector` column dimension is fixed by migration. `ORBIT_EMBEDDING_DIMENSIONS`
must agree with it, and **the application refuses to start if it does not** — a
mismatch would otherwise corrupt silently. Every chunk row also records its
`embedding_model` and chunker configuration version, so "which documents need
re-embedding" is a query, not a guess (ADR-0007, [ADR-0013](../decisions/0013-chunking-strategy.md)).

## 5. Object storage

Detailed in [ADR-0011](../decisions/0011-object-storage-and-upload.md).

The `ObjectStorage` port takes and returns **streams, never `bytes`** — a
signature accepting `bytes` guarantees some caller eventually loads 50 MiB into
memory, and that N concurrent uploads become an OOM kill.

```
workspaces/{workspace_id}/documents/{document_id}/{content_sha256}
```

Every path component is server-generated. The original filename is a database
column, never a path segment: path traversal is unrepresentable rather than
mitigated.

- **Uploads are proxied** through the API and validated while streaming — byte
  count, SHA-256, magic-byte sniffing — with the request aborted the moment a
  limit or type check fails.
- **Storage commits before the database.** An orphaned object is invisible and
  reclaimable; a document row with no bytes is broken and visible. A scheduled
  sweep reclaims orphans.
- **Downloads are presigned** for 60 seconds *after* the API authorizes the
  request. The API leaves the data path; access control does not.
- One boto3-backed adapter serves both MinIO and S3, differing only by endpoint
  and addressing style.

## 6. Background processing

Celery 5, prefork pool, Redis broker ([ADR-0002](../decisions/0002-celery-prefork-job-queue.md)).

| Setting | Value | Reason |
|---|---|---|
| `worker_prefetch_multiplier` | `1` | The default of 4 lets one worker reserve four long jobs while another idles |
| `task_acks_late` | `true` | Acknowledge after completion, so a killed child redelivers |
| `task_time_limit` | hard, SIGKILL | Terminates a wedged parser that will not yield |
| `task_soft_time_limit` | below the hard limit | Lets the task record `FAILED` with a reason first |
| `worker_max_memory_per_child` | set | Contains leaks and allocation spikes |
| `worker_max_tasks_per_child` | set | Bounds native-library state corruption |

Task payloads carry **identifiers only, never document content** — Redis is not
a document store, and payloads reach logs and dashboards.

Retry policy follows the error taxonomy of ADR-0014: transient dependency
failures retry with jittered exponential backoff; client errors (unsupported
type, corrupt file) fail permanently and immediately. **Retrying a permanent
failure is a cost multiplier with no chance of success.**

## 7. Configuration

One `Settings` object built from environment variables via `pydantic-settings`,
validated at startup, `ORBIT_`-prefixed. Nothing reads `os.environ` directly.

**Startup fails loudly** on a missing secret, an embedding dimension that
disagrees with the schema, a wildcard CORS origin combined with credentials, or
a production environment with development defaults. A misconfigured process that
refuses to start is strictly better than one that starts and behaves subtly
wrongly.
