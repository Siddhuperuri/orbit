# Data flow

How data moves through ORBIT, and what happens at each boundary when it goes
wrong. Components are described in [backend.md](backend.md); the retrieval and
generation stages are in [ai-pipeline.md](ai-pipeline.md).

---

## 1. Document upload

```
client ── POST /api/v1/workspaces/{ws}/documents  (multipart)
   │
   ├─ authorize: caller is a member of {ws} with document:create
   │
   ├─ stream bytes, and as they pass:
   │     count      → abort past ORBIT_MAX_UPLOAD_BYTES
   │     sha256     → deduplication + integrity
   │     sniff      → real content type from magic bytes
   │
   ├─ reject on: oversize │ unsupported type │ declared/actual mismatch │ empty
   │
   ├─ if (workspace_id, sha256) already exists → return the existing document, 200
   │
   ├─ ① write object   workspaces/{ws}/documents/{doc}/{sha256}
   │
   ├─ ② transaction:  INSERT document (PENDING)  +  INSERT job (QUEUED)   ── commit
   │
   ├─ ③ enqueue Celery task { document_id, job_id, request_id }
   │
   └─ 201 Created + document resource (processing has not started; poll
        GET …/documents/{id}/processing)
```

**The declared `Content-Length` and `Content-Type` are hints, never controls.**
Both are attacker-controlled. The byte counter and the magic-byte sniff are the
controls ([ADR-0011](../decisions/0011-object-storage-and-upload.md)).

**Ordering matters and is deliberate.** Object storage and PostgreSQL cannot
share a transaction, so one of two inconsistencies is always possible:

| If | Result | Severity |
|---|---|---|
| ② fails after ① | Orphaned object | Invisible to users; reclaimed by sweep |
| ① fails after ② | Document row with no bytes | Visible, broken, pipeline fails |

Storage first. An orphan costs money; a dangling row costs correctness.

**If ③ fails after ② commits**, the document sits in `PENDING` with a queued job
and no message. A reconciliation sweep re-enqueues jobs that have been `QUEUED`
beyond a threshold — the queue is not treated as reliable, because Redis is
explicitly disposable.

## 2. Document processing

Runs in the Celery worker under prefork isolation and hard limits
([ADR-0002](../decisions/0002-celery-prefork-job-queue.md)). The job row, not the
broker message, is the source of truth
([ADR-0019](../decisions/0019-asynchronous-processing-pipeline.md)).

```
message { job_id, request_id }
   │
claim ── SELECT job, version FOR UPDATE SKIP LOCKED
   │      decide: finished → no-op │ running, lease live → no-op │ lease expired → abandon
   │              not due → no-op  │ superseded → FAILED           │ due → RUNNING + lease
   ▼
PROCESSING   (lease renewed and stage recorded at each boundary below)
   │
   ├─ fetch      ── stream object to a spooled temp file; byte count and sha256
   │                must match the upload record
   ├─ parse      ── registry by sniffed content type → ExtractedDocument (ADR-0012)
   ├─ normalize  ── NFC, ligatures, invisibles, whitespace, de-hyphenation,
   │                running headers/footers, page-split paragraphs → ParsedDocument
   │                (empty → DOCUMENT_EMPTY; PDF without text → NO_EXTRACTABLE_TEXT)
   ├─ chunk      ── structure-aware, token-bounded, overlapped, deduplicated (ADR-0013)
   ├─ embed      ── batched through EmbeddingProvider, float32 in memory (ADR-0007)
   └─ index      ── ONE transaction: fenced job→SUCCEEDED, lock version (still
                    current?), delete version's chunks, insert new, version→READY
   ▼
READY   or   PENDING (retry scheduled)   or   FAILED(code, user-safe reason)
```

Every stage emits `pipeline.stage_completed` with its duration and output
(pages, blocks, characters, chunks, tokens), under the upload's `request_id`,
`document_id`, and `job_id`.

### The state machine

Two machines, deliberately separate.

**Version** — what the user sees:

```
PENDING ──claim──▶ PROCESSING ──index──▶ READY
   ▲                   │
   └── transient ──────┤
                       └── permanent │ defect │ retries exhausted ──▶ FAILED
FAILED ──reprocess──▶ PENDING   (explicit, fresh retry budget)
```

**Job** — one row per attempt, what an operator sees:

```
QUEUED ──claim──▶ RUNNING(worker_id, lease_expires_at, stage) ──▶ SUCCEEDED
                     │
                     └──▶ FAILED(error_code, failure_kind, error_message)
```

**`FAILED` means the document is the problem. `PENDING` means we are.** An
embedding provider outage returns the version to `PENDING` with the next attempt
queued — until the retry budget runs out, when it fails with
`PROCESSING_RETRIES_EXHAUSTED` and a reason that says the file is fine.

Constraints make ambiguous states unrepresentable: at most one `QUEUED`/`RUNNING`
job per version; a `RUNNING` job always has a holder and a deadline; a `FAILED`
job always has a code and a kind; a `READY` version always has chunks.

### Idempotency and worker death

- **Duplicate delivery** is a no-op: claiming is a locked, conditional decision.
- **A killed worker** stops renewing its lease. When the lease expires, recovery
  (at worker start, on the beat schedule, or when the redelivered message
  arrives) records `WORKER_LOST` and schedules the next attempt.
- **A presumed-dead worker that wakes up** is fenced: every write it makes is
  `WHERE worker_id = :me`, matches nothing, and its index transaction rolls back
  with every chunk it inserted.
- **A crash inside the index transaction** leaves no chunks: delete, insert, and
  `READY` commit together or not at all.
- **Parsing, normalization, and chunking are deterministic**, so a re-run produces
  the same chunks.
- **A newer version uploaded mid-run** demotes the old version and deletes its
  chunks under the document lock; the old job finds it no longer current when it
  locks the version to index, and records `DOCUMENT_VERSION_SUPERSEDED`.
- **A lost message** is re-published by recovery once `enqueued_at` is older than
  the grace period.

### Failure classification

| Class | Examples | Behaviour |
|---|---|---|
| **Permanent** | Corrupt or encrypted PDF, no extractable text, empty file, undecodable text, limits exceeded, integrity mismatch, missing object, time limit | `FAILED` now with a user-safe reason. **No retry.** |
| **Transient** | Provider 429/5xx or timeout, storage or database unreachable, deadlock, worker lost | Next attempt after jittered exponential backoff; version back to `PENDING`; bounded by `ORBIT_PROCESSING_MAX_ATTEMPTS` |
| **Defect** | Any unrecognised exception, provider credential rejection | `FAILED` now with a generic reason; traceback logged; no retry |

Retrying a permanent failure is a cost multiplier with no chance of success. The
classification is the same taxonomy the HTTP layer uses
([ADR-0014](../decisions/0014-error-handling-strategy.md)).

Each failure carries **two messages**: `failure_reason` on the version, written
for the uploader; `error_message` on the job row and in the logs, for the
operator. The status endpoint returns only the first.

## 3. Search

```
query ── validate, bound length
   │
   ├─ embed query                   ─┐
   ├─ to_tsquery(query)             ─┤ both carry workspace_id in the WHERE clause
   │                                 │
   ├─ dense:   HNSW cosine, top N   ─┤
   ├─ lexical: GIN ts_rank_cd, top N ┘
   │
   ├─ RRF fusion   score = Σ 1/(60 + rank_i)
   ├─ optional rerank (port; no-op by default)
   └─ page of results + highlights
```

Tenant isolation lives **inside** each retrieval query, not in a post-filter
([ADR-0004](../decisions/0004-authorization-in-repositories.md)). A post-filter
would first retrieve another tenant's most relevant content and then discard it,
which is a leak waiting for one refactor to become visible.

Detail in [ADR-0005](../decisions/0005-hybrid-retrieval-rrf.md).

## 4. Ask a question

```
question ── retrieve (as above)
   │
   ├─ assign per-request handles  S1..Sn  →  chunk_id   (server-side map)
   ├─ construct context under a token budget, deduplicated by adjacency
   ├─ LLM stream ──▶ tokens to client as they arrive
   │
   └─ on completion:
        extract handles from the answer
        resolve each against the request-scoped map
        DROP every handle that does not resolve   ← counted, logged, alerted
        build citation metadata FROM THE DATABASE, never from model output
```

There is no code path from generated tokens to citation metadata
([ADR-0006](../decisions/0006-bound-citations.md)). A fabricated reference
cannot become a citation, because citations are *resolved*, not parsed.

## 5. Authentication

```
POST /auth/login
   ├─ verify Argon2id; re-hash if parameters have been raised since
   ├─ issue access JWT (15 min)  → HttpOnly cookie, Path=/api
   └─ issue refresh token        → HttpOnly cookie, Path=/api/v1/auth
                                   256-bit, stored SHA-256 hashed, family id

POST /auth/refresh
   ├─ look up by hash
   ├─ if already consumed → REUSE DETECTED → revoke the whole family, 401
   ├─ mark consumed, issue a new pair in the same family
   └─ set both cookies

POST /auth/logout
   └─ revoke family; clear both cookies with matching attributes
```

A consumed token can only be presented if it was captured, so reuse revokes the
family rather than being ignored
([ADR-0003](../decisions/0003-authentication.md),
[ADR-0009](../decisions/0009-single-origin-cookie-transport.md)).

## 6. Deletion

Deletion crosses three stores that cannot share a transaction, so order is again
chosen by which inconsistency is tolerable.

```
DELETE /documents/{id}
   ├─ authorize
   ├─ soft-delete the document row          ── immediately invisible to the user
   ├─ commit
   └─ enqueue reclamation:
         hard-delete chunks and embeddings  (derived, rebuildable)
         delete the object
         finally hard-delete the row after the retention window
```

The user's expectation — "it is gone" — is satisfied at the first commit.
Reclamation is asynchronous and idempotent, and can be retried safely.

**Soft delete for user-facing entities** (documents, folders, workspaces),
because an accidental delete of someone's corpus must be recoverable. **Hard
cascade for derived data** — chunks and embeddings are worthless without their
document and cheap to rebuild.

Every query filters soft-deleted rows by default; including them is explicit and
restricted to administrative paths.

## 7. Correlation across all of it

A `request_id` (ULID) is assigned at the edge, bound to a contextvar, attached
to every log record, **carried in the Celery payload**, restored in the worker,
and reused across retries
([ADR-0015](../decisions/0015-observability-strategy.md)).

The upload request, the enqueue, three worker attempts, and the final failure
all share one identifier. Without that, investigating "my document never became
ready" begins by grepping timestamps across two processes.
