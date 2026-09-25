# 0024 — What Redis is used for, and what happens when it is gone

- **Status:** Accepted
- **Date:** 2026-09-24
- **Relates to:** [ADR-0002](0002-celery-prefork-job-queue.md) (the broker),
  [ADR-0004](0004-authorization-in-repositories.md) (where authorization is
  decided), [ADR-0017](0017-rate-limiting.md) (the limiter),
  [ADR-0019](0019-asynchronous-processing-pipeline.md) (job recovery),
  [ADR-0021](0021-hybrid-search-baseline.md) (search)

## Context

Redis was already in the deployment as the Celery broker and the rate-limit
counter. The open question was how much further it should go — caching,
temporary state, coordination — and the honest answer required asking a
different question first: **what breaks when it disappears?**

That framing matters because Redis is the one dependency ORBIT is explicitly
willing to lose. PostgreSQL going away means nothing works and the instance
should leave rotation. Redis going away should mean ORBIT is *slower and less
protected*, not *down*. Every use added to Redis is therefore a bet that the
feature can survive the backing store vanishing mid-request — and a cache that
cannot is not a cache, it is an undeclared second source of truth.

The tempting uses are exactly the dangerous ones. The hottest query in the
system is the membership lookup behind `ResolveAccessContext`, which runs on
every workspace-scoped request. Caching it would remove one indexed primary-key
lookup per request and would be the single worst change available.

## Decision

### Four uses, and the reason each one is safe

| Use | What is stored | Why losing it is survivable |
|---|---|---|
| **Job queue** (ADR-0002) | Celery task messages | Job *rows* live in PostgreSQL. A lost message is re-published by the recovery sweep after a grace period; a lost broker delays processing, it never loses a document |
| **Rate limiting** (ADR-0017) | Fixed-window counters | Fails **open**: the limiter reports "allowed" and logs. Argon2id still makes each credential guess expensive, and `/readyz` shows Redis down |
| **Query-vector cache** (new) | The embedding of a search query | Fails **open**: a miss. The provider is called, exactly as on any cold key |
| **Celery result backend** | Task results, expiring at one hour | Job state is in PostgreSQL. The result backend is for operational inspection only |

### Nothing whose correctness depends on authorization is cached

There is no general-purpose `Cache` port that a use case can reach for. The
only caching port is `QueryVectorCache`, and it holds the output of a pure
function of text.

This is not caution about a hypothetical. Caching an authorization decision
fails in one specific, silent direction:

- **Revocation is the problem.** A cached membership means a user removed from
  a workspace keeps reading it until the entry expires. A TTL short enough to
  make that acceptable is short enough that the cache rarely hits — so the
  feature costs a correctness risk and buys nothing.
- **Invalidation-on-write does not rescue it.** The write that must invalidate
  is a role change *in another process*. A missed invalidation fails open,
  silently, in the direction of granting access. There is no alarm for "this
  cache was not cleared".
- **The thing being avoided is cheap.** `ResolveAccessContext` is one indexed
  primary-key lookup against a table PostgreSQL has entirely in memory.

Result pages, document lists, and processing status are likewise uncached. All
three are *derived from* rows whose visibility can change, which puts them in
the same category with a longer fuse.

### The query-vector cache, and why its key looks the way it does

```
orbit:qvec:{workspace_id}:{model}@{dimensions}:{sha256(query_text)}
```

A query vector is a deterministic function of (text, embedding space). A hit
cannot be stale in the way a cached document list can, and it saves a paid
external round trip on the latency path of every search and every question.

Each key component is load-bearing:

- **`workspace_id` — the tenant scope.** The value itself is
  tenant-independent, so this is not confusion about what it contains; it is a
  side-channel control. A process-wide cache would let a member of workspace A
  measure, purely from response latency, whether anyone in workspace B had
  recently searched a given phrase. The cost of scoping is a lower hit rate on
  phrases two tenants happen to share. The same reasoning already governs
  vector *reuse* during indexing (ADR-0020).
- **`model@dimensions` — the embedding space.** Vectors from two models are
  not comparable. Without this, changing the model would serve the old space's
  vectors into the new space's index and produce rankings from meaningless
  distances. With it, a model change self-invalidates: no flush, no operator
  step, no window.
- **`sha256(query_text)` — the query, hashed.** Redis keys appear in
  `SLOWLOG`, `MONITOR`, and any keyspace dump. A cache of raw search phrases
  would turn all three into a readable log of what every tenant is looking
  for. Same reasoning as the rate limiter's account keys.

An unexpected consequence worth naming: because a cached vector needs no
provider call, a query embedded *before* an AI outage keeps its semantic half
*during* the outage. The cache is a small buffer against provider failure, not
only a cost saving.

### Timeouts are configuration, not constants

Every external client is bounded, and every bound is a setting rather than a
literal, because the right value depends on where the dependency sits and
because these are the numbers an operator needs under load:

| Dependency | Settings | Default |
|---|---|---|
| PostgreSQL | `ORBIT_DB_STATEMENT_TIMEOUT_SECONDS`, `ORBIT_DB_POOL_TIMEOUT_SECONDS` | 15 s, 10 s |
| PostgreSQL (worker) | `ORBIT_WORKER_DB_STATEMENT_TIMEOUT_SECONDS` | 120 s |
| Redis | `ORBIT_REDIS_CONNECT_TIMEOUT_SECONDS`, `ORBIT_REDIS_COMMAND_TIMEOUT_SECONDS` | 2 s, 2 s |
| Object storage | `ORBIT_S3_CONNECT_TIMEOUT_SECONDS`, `ORBIT_S3_READ_TIMEOUT_SECONDS` | 3 s, 10 s |
| Embeddings | `ORBIT_EMBEDDING_REQUEST_TIMEOUT_SECONDS` | 60 s |
| Language model | `ORBIT_LLM_REQUEST_TIMEOUT_SECONDS`, `ORBIT_ANSWER_GENERATION_TIMEOUT_SECONDS` | 30 s, 60 s |

`statement_timeout` is the one worth calling out. A client-side deadline stops
*this coroutine* waiting; the query keeps running, keeps its locks, and keeps
its connection checked out. Only PostgreSQL cancelling the statement actually
returns the resource. The API and the worker get different ceilings because a
request that has not answered in fifteen seconds has already failed for its
caller, while a re-index batch legitimately runs for minutes.

### Search is rate limited

`POST /workspaces/{id}/search` is metered per account and per client address,
under its own `search` scope. Every hybrid query costs a paid embedding request
plus a full-text scan and an HNSW probe: unmetered, it is the cheapest way to
spend an operator's AI budget from one valid account, and the cheapest way to
saturate the database from one.

It is a separate scope from `chat` rather than a shared budget, because a
person refining a query legitimately issues a burst of searches in a way they
never issue a burst of questions. `AnswerQuestion`'s internal search is
explicitly exempt (`metered=False`) — it has already spent the stricter chat
budget, and charging both would mean a user asking questions gradually loses
the ability to search.

## Alternatives considered

**A membership / access-context cache.** Rejected above, at length. It is the
change with the best-looking benchmark and the worst failure mode in this
document.

**Caching search result pages.** Rejected. The value is derived from rows whose
visibility changes — a document archived, deleted, or moved between folders,
or a member's role changed. Every one of those makes a cached page wrong, and
"wrong" here means showing a user a document they can no longer see. The
query-vector cache captures most of the latency win with none of that.

**Fail-closed rate limiting.** Rejected in ADR-0017 and re-affirmed here.
Failing closed converts a Redis outage into a total authentication outage,
including for the operators trying to fix it. Fail-open is only defensible
because it is *visible* — it logs at ERROR and `/readyz` already reports Redis
down.

**Rejecting uploads when the broker is unreachable.** Rejected, and this
reverses what an earlier draft of `system.md` claimed. The upload's durable
half does not involve Redis: the object is stored, and the document, version,
and processing job commit in one transaction. Only the *publish* needs the
broker, and a failed publish merely delays the job until the recovery sweep
re-publishes it (ADR-0019). Refusing the upload would report a failure that did
not happen and discard bytes the user already sent.

**A Redis-backed circuit breaker shared across API processes.** Rejected for
now. It would let one process's discovery that the provider is dead protect
every other process, which is genuinely useful. It also makes provider
availability depend on Redis, which contradicts the first paragraph of this
document, and it introduces a shared mutable state whose staleness has its own
failure modes. The per-process breaker in `infrastructure/ai/resilient_llm.py`
converges within a few requests per process, which is enough at ORBIT's scale.
Revisit when the API is wide enough that per-process convergence is measurably
wasteful.

**Redis-backed sessions.** Rejected. Sessions are refresh-token rows in
PostgreSQL (ADR-0003). Moving them to Redis would mean a Redis restart signs
every user out.

## Consequences

- A Redis outage degrades ORBIT along four axes — slower search, unmetered
  request rates, delayed document processing, no task results — and takes
  nothing down. This is verified, not asserted:
  `backend/tests/integration/test_failure_modes.py`.
- Authorization stays a database question. Every workspace request pays one
  indexed lookup, and nothing can be stale.
- The AI budget has a ceiling that does not depend on users behaving well.
- Changing the embedding model invalidates the query cache for free, which
  removes a class of "I changed the model and search got worse" incidents.
- Failure behaviour is documented per dependency, with the runbook entry
  beside it: [docs/operations/failure-modes.md](../operations/failure-modes.md).
