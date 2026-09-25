# 0007 — AI access behind ports, with a deterministic fake as the default

- **Status:** Accepted — amended by [0020](0020-vector-indexing.md) (embedding port shape, retries, schema check)
- **Date:** 2026-09-09

## Context

AI providers are, from an engineering standpoint, unusually hostile
dependencies. They are slow (seconds, not milliseconds), non-deterministic,
metered per call, rate-limited, subject to unannounced model deprecation, and
they fail in ways that are not always distinguishable from a bad prompt.

A codebase with `openai.chat.completions.create(...)` scattered across it
inherits every one of those properties in every place the call appears. Section
13 of the brief is explicit: AI is a subsystem, not the architecture.

There is a second, load-bearing requirement in section 13: the application must
remain testable without a live provider. Any test that calls a real API is slow,
costs money, fails when the network does, and is not deterministic — so it will
eventually be skipped, and then deleted.

## Context: the dimension problem

`pgvector` column dimensionality is fixed at migration time. The provider choice
therefore reaches into the database schema, which makes it far less swappable in
practice than an interface suggests. This has to be stated plainly rather than
hidden behind the abstraction.

## Decision

Three ports, declared in `orbit.domain.ports`, implemented in
`orbit.infrastructure.ai`:

| Port | Responsibility |
|---|---|
| `EmbeddingProvider` | Text → vectors. Batched. Declares its `dimensions` and `model_id`. |
| `LLMProvider` | Messages → completion, streaming or buffered. Reports token usage. |
| `Reranker` | (query, candidates) → reordered candidates. Default implementation is a no-op. |

Implementations:

- **`FakeEmbeddingProvider` / `FakeLLMProvider` — the default**
  (`ORBIT_AI_PROVIDER=fake`). Deterministic, offline, zero-cost. Embeddings are
  derived from a seeded hash of the text, so identical text always yields an
  identical vector and semantically identical queries retrieve consistently.
  The full application — upload, processing, retrieval, chat, citations — runs
  under the fake with no credentials. This is a supported operating mode, not a
  test stub.
- **`OpenAIEmbeddingProvider` / `OpenAILLMProvider`** —
  `text-embedding-3-small` (1536 dimensions) and `gpt-4o-mini`. Selected as the
  first real adapter for cost, latency, and ecosystem maturity.

Cross-cutting concerns — timeouts, bounded retry with jittered backoff, circuit
breaking, token accounting, latency metrics — are implemented **once, in a
decorator around the port**, not per provider. A new provider inherits all of it.

The concrete embedding dimension is recorded in configuration
(`ORBIT_EMBEDDING_DIMENSIONS`) *and* on every chunk row alongside its
`embedding_model`. The application refuses to start if configuration disagrees
with the schema, which turns a silent corruption into a startup failure.

## Amendments (ADR-0020)

- The embedding port declares an `EmbeddingSpace` (model **and** width) rather
  than a bare model id, and has separate `embed_documents` / `embed_query`.
- "Refuses to start" is implemented as a **readiness** refusal plus a worker-side
  check before any provider call: startup deliberately makes no database call
  (M1), so the API's `/readyz` reports `embedding_schema` down on a mismatch.
- The retry decorator retries individual requests in-process for seconds; the
  job's durable backoff (ADR-0019) handles anything longer. No circuit breaker:
  worker containers are per task, so a breaker's state would not survive one
  document.
- The fake provider never takes the configured model id.

## Amendments (ADR-0022)

- `LLMProvider` and `Reranker` are implemented. The LLM decorator
  (`ResilientLLMProvider`) **does** have a circuit breaker: the API is a
  long-lived process, so its state is meaningful there, unlike in the worker.
  It retries a stream only before the stream's first event.
- `FakeLLMProvider` answers extractively from the sources in its prompt and
  declares insufficient evidence when none overlap the question;
  `fabricate_citations=True` makes it cite handles it was never given.
- The OpenAI chat adapter speaks `/chat/completions` over `httpx`, like the
  embedding adapter; no vendor SDK is a dependency.

## Alternatives considered

**Direct SDK calls at call sites.** Rejected: untestable offline, and vendor
coupling spread across the codebase.

**LangChain / LlamaIndex as the abstraction.** Rejected. They are frameworks,
not abstractions: adopting one means adopting its control flow, its prompt
handling, and its release cadence for the whole retrieval and chat path. The
surface actually needed here is three methods. The dependency cost — transitive
packages, breaking changes, opaque behaviour when debugging a bad answer — is
not repaid.

**Recording real provider responses (VCR-style cassettes).** Useful, and may be
added later for a small set of contract tests against the real API. Rejected as
the primary test strategy: cassettes go stale, and they cannot exercise the
adversarial cases — a model that fabricates citations, returns an empty
response, or times out mid-stream — that the fake produces on demand.

**Storing embeddings from multiple models in one column.** Rejected: dimensions
must match, and mixing model outputs in one vector space produces meaningless
distances.

## Consequences

- The entire test suite and a full local development loop run with **no API key
  and no network**. This is verified in CI, which has no provider credentials.
- Changing embedding provider or model is a **data migration**, not a
  configuration change: it requires re-embedding the corpus and rebuilding the
  HNSW index. The procedure is documented in `docs/database/embeddings.md`, and
  the `embedding_model` column exists so the scope of such a migration is
  queryable.
- Retrieval quality under the fake provider is meaningless; it is deterministic,
  not semantic. Retrieval benchmarks must therefore run against a real provider,
  and are reported separately in `docs/benchmarks/retrieval.md`.
- Provider outage is an expected condition, not an exception. Chat degrades to a
  clear error with a retry affordance; document processing retries with backoff
  and holds the document in `PENDING` rather than marking it `FAILED`, because
  the document is fine and the provider is not.
