# Architecture Decision Records

Each ADR captures one decision that was expensive to make and would be expensive
to reverse. They are immutable once accepted: a decision that changes gets a new
ADR that supersedes or amends the old one, so the reasoning history stays intact.

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-modular-monolith.md) | Modular monolith with two runtime entrypoints | Accepted |
| [0002](0002-celery-prefork-job-queue.md) | Celery with a prefork pool for document processing | Accepted — worker DB access amended by 0019 |
| [0003](0003-authentication.md) | Argon2id passwords, short-lived access + rotating refresh tokens | Accepted — transport amended by 0009 |
| [0004](0004-authorization-in-repositories.md) | Authorization enforced at the repository layer | Accepted |
| [0005](0005-hybrid-retrieval-rrf.md) | Hybrid retrieval in PostgreSQL fused with RRF | Accepted |
| [0006](0006-bound-citations.md) | Citations resolved against retrieved chunks, never generated | Accepted |
| [0007](0007-ai-provider-ports.md) | AI access behind ports, deterministic fake as default | Accepted |
| [0008](0008-npm-task-runner.md) | npm scripts as the single cross-platform task runner | Accepted |
| [0009](0009-single-origin-cookie-transport.md) | Single-origin deployment and cookie-based token transport | Accepted — amends 0003 |
| [0010](0010-database-access-pattern.md) | Repositories with an explicit unit of work, no lazy loading | Accepted |
| [0011](0011-object-storage-and-upload.md) | Object storage abstraction, proxied upload, presigned download | Accepted |
| [0012](0012-document-parser-architecture.md) | Parsers produce normalized text with provenance | Accepted |
| [0013](0013-chunking-strategy.md) | Structure-aware, token-bounded chunking with overlap | Accepted |
| [0014](0014-error-handling-strategy.md) | Typed domain errors mapped once to a stable envelope | Accepted |
| [0015](0015-observability-strategy.md) | Structured logs and metrics now, tracing deferred | Accepted |
| [0016](0016-frontend-data-fetching.md) | TanStack Query owns application data; RSC owns the shell | Accepted |
| [0017](0017-rate-limiting.md) | Per-account and per-IP fixed windows in Redis, failing open | Accepted |
| [0018](0018-account-lifecycle.md) | Hashed single-use tokens for reset and verification; no fake mail sender | Accepted |
| [0019](0019-asynchronous-processing-pipeline.md) | Durable jobs with leases and fencing; the database, not the broker, owns work | Accepted — amends 0002 |
| [0020](0020-vector-indexing.md) | Vector indexing in pgvector: self-describing embedding spaces, reuse, verified READY, in-place re-index | Accepted — amends 0007 |
| [0021](0021-hybrid-search-baseline.md) | Hybrid search baseline: coverage-ranked full-text + pgvector, fused by RRF; authorization inside both retrievers | Accepted — implements 0005 |
| [0022](0022-grounded-question-answering.md) | Grounded question answering: two-phase pipeline, stage-typed failures, one answer row per question | Accepted — implements 0006 |
| [0023](0023-document-organization.md) | Document organization: archive as a flag, empty-only folder deletion under row locks, idempotent tags, sort-bound cursors, honest progress, viewer = indexed text | Accepted |
| [0024](0024-redis-usage-and-degradation.md) | Redis holds queue, counters, and query vectors only; no authorization or result caching; every timeout configurable; search metered; documented degradation per dependency | Accepted — extends 0002, 0017 |

## By concern

Where to look when a specific question comes up.

| Question | ADR |
|---|---|
| How do users authenticate? | [0003](0003-authentication.md) + [0009](0009-single-origin-cookie-transport.md) |
| Where do tokens live, and why not `localStorage`? | [0009](0009-single-origin-cookie-transport.md) |
| How is one tenant kept out of another's data? | [0004](0004-authorization-in-repositories.md) |
| Why Celery and not an async queue? | [0002](0002-celery-prefork-job-queue.md) |
| What happens when a worker dies mid-document? How do retries work? | [0019](0019-asynchronous-processing-pipeline.md) |
| Why pgvector and not a vector database? What happens when the embedding model changes? | [0020](0020-vector-indexing.md) |
| How is a search ranked, and how is another tenant's content kept out of it? | [0021](0021-hybrid-search-baseline.md) |
| How is the database accessed? Why does a relationship raise? | [0010](0010-database-access-pattern.md) |
| Why are uploads proxied instead of presigned? | [0011](0011-object-storage-and-upload.md) |
| How is a new document format added? | [0012](0012-document-parser-architecture.md) |
| Why these chunk sizes? | [0013](0013-chunking-strategy.md) |
| How is a vendor kept out of the business logic? | [0007](0007-ai-provider-ports.md) |
| Why hybrid search, and why RRF? | [0005](0005-hybrid-retrieval-rrf.md) |
| How is a fabricated citation prevented? | [0006](0006-bound-citations.md) |
| How does the frontend fetch data? | [0016](0016-frontend-data-fetching.md) |
| What does an API error look like? | [0014](0014-error-handling-strategy.md) |
| How is a production failure investigated? | [0015](0015-observability-strategy.md) |
| What stops password guessing, and what happens when Redis is down? | [0017](0017-rate-limiting.md) |
| How does a locked-out user recover an account? | [0018](0018-account-lifecycle.md) |
| Why is there no fake email provider? | [0018](0018-account-lifecycle.md) |

## Deliberately deferred

Decisions consciously postponed, each with its trigger recorded in the ADR that
rejected it — so the absence is a decision, not an oversight.

| Deferred | Revisit when | ADR |
|---|---|---|
| PostgreSQL row-level security | Connection-scoping is proven under load | [0004](0004-authorization-in-repositories.md) |
| Dedicated search engine | Retrieval latency or corpus size demands it | [0005](0005-hybrid-retrieval-rrf.md) |
| External identity provider | SSO/SAML is required | [0003](0003-authentication.md) |
| Presigned direct upload | Upload bandwidth constrains the API | [0011](0011-object-storage-and-upload.md) |
| Semantic chunking | Benchmarks show structure-aware chunking is the bottleneck | [0013](0013-chunking-strategy.md) |
| Cross-encoder reranking | Fusion alone measurably underperforms | [0005](0005-hybrid-retrieval-rrf.md) |
| OpenTelemetry tracing | A third service exists, or logs cannot answer a latency question | [0015](0015-observability-strategy.md) |
| Postgres-backed queue | Removing Redis becomes worth hand-building retry semantics | [0002](0002-celery-prefork-job-queue.md) |
| Sliding-window or token-bucket limiting | Boundary bursts prove to matter in practice | [0017](0017-rate-limiting.md) |
| Enforced email verification | A feature exists whose abuse actually requires it | [0018](0018-account-lifecycle.md) |
| A real mail provider adapter | Deployment to an environment that must send mail | [0018](0018-account-lifecycle.md) |
| A dedicated vector database | Measured p95 or recall at the real corpus size misses target with pgvector tuned | [0020](0020-vector-indexing.md) |
| Embeddings table keyed by space | Two embedding spaces must be live at once | [0020](0020-vector-indexing.md) |
| Reusing vectors across versions of one document | Re-embedding cost of edited documents becomes material | [0020](0020-vector-indexing.md) |
| Query rewriting for follow-up questions | Elliptical follow-ups measurably retrieve poorly | [0022](0022-grounded-question-answering.md) |
| An explicit cancel endpoint | Closing the stream proves insufficient as cancellation | [0022](0022-grounded-question-answering.md) |
| A shared, Redis-backed circuit breaker | The API fleet is wide enough that per-process convergence is measurably wasteful | [0024](0024-redis-usage-and-degradation.md) |
| Caching result pages or access contexts | Never — the revocation and staleness costs are argued, not deferred | [0024](0024-redis-usage-and-degradation.md) |

## Template

```markdown
# NNNN — Title

- **Status:** Proposed | Accepted | Superseded by ADR-XXXX
- **Date:** YYYY-MM-DD

## Context
## Decision
## Alternatives considered
## Consequences
```

An ADR without a genuine "Alternatives considered" section is a description, not
a decision. If nothing was rejected, nothing was decided.
