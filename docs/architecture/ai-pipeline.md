# AI pipeline

AI is a **subsystem**, not the architecture. Everything here sits behind ports
declared in `orbit.domain.ports` and is replaceable without touching a use case.

Governing decisions: [ADR-0007](../decisions/0007-ai-provider-ports.md) (ports
and the fake provider), [ADR-0005](../decisions/0005-hybrid-retrieval-rrf.md)
(hybrid retrieval), [ADR-0006](../decisions/0006-bound-citations.md) (citations),
[ADR-0013](../decisions/0013-chunking-strategy.md) (chunking).

---

## 1. Layering

```
application (use cases)
      │  depends only on ports
      ▼
domain.ports:  EmbeddingProvider   LLMProvider   Reranker
      │
      ▼
resilience decorator — timeout, bounded retry + jittered backoff,
                       circuit breaker, token accounting, latency metrics
      │                (implemented ONCE, not per provider)
      ▼
infrastructure.ai:  FakeEmbedding/FakeLLM  │  OpenAIEmbedding/OpenAILLM
```

Cross-cutting concerns live in the decorator. A new provider inherits timeouts,
retries, circuit breaking, and instrumentation for free — and cannot forget them.

**No use case imports a vendor SDK.** The import contract enforces it: adapters
live in `infrastructure`, which `application` cannot import.

## 2. Ports

```python
class EmbeddingProvider(Protocol):
    space: EmbeddingSpace          # model + dimensions: the identity of the vector space
    max_batch_size: int
    max_batch_tokens: int
    async def embed_documents(self, texts: Sequence[str]) -> Sequence[Vector]: ...
    async def embed_query(self, text: str) -> Vector: ...

class LLMProvider(Protocol):
    model_id: str
    context_window: int
    async def complete(self, messages: Sequence[LLMMessage], *,
                       max_tokens: int, temperature: float) -> Completion: ...
    def stream(self, messages: Sequence[LLMMessage], *,
               max_tokens: int, temperature: float) -> AsyncIterator[TextDelta | StreamEnd]: ...

class Reranker(Protocol):
    name: str
    async def rerank(self, query: str, results: Sequence[SearchResult],
                     *, top_k: int) -> Sequence[SearchResult]: ...
```

A stream ends with exactly one `StreamEnd` (finish reason, token usage, model
id). A stream that stops without one **raises**: "the connection dropped" must
never be mistaken for "the model finished".

`embed_documents` and `embed_query` are separate methods because some models
require different prefixes or instructions for each side, and a single `embed`
would silently produce a worse retrieval space.

Both providers declare their properties — `dimensions`, `context_window` — so
the application can validate configuration at startup rather than discovering a
mismatch mid-request.

## 3. The fake provider is a supported mode

`ORBIT_AI_PROVIDER=fake` is the **default**, not a test fixture.

- Embeddings are derived from a seeded hash of the text and L2-normalised.
  Identical text always yields an identical vector, so retrieval is reproducible.
- The fake LLM produces deterministic answers and, **on demand, deliberately
  fabricates citation handles** — which is what makes the guarantee in ADR-0006
  testable rather than asserted.

The entire application — upload, processing, retrieval, chat, citations — runs
with no API key and no network. CI has no provider credentials, which is what
keeps this true rather than aspirational.

Retrieval *quality* under the fake is meaningless; it is deterministic, not
semantic. Quality is measured separately against a real provider and recorded in
`docs/benchmarks/retrieval.md`.

## 4. Ingestion side

```
ParsedDocument ─▶ normalize ─▶ chunk ─▶ embed (batched) ─▶ persist
```

- **Batched** by `ORBIT_EMBEDDING_BATCH_SIZE` and an approximate token budget.
  One call per chunk would be slow and expensive.
- **A failed request is retried in-process** — a 429 on batch 7 of 12 retries
  batch 7 for up to `ORBIT_EMBEDDING_RETRY_MAX_WAIT_SECONDS`, it does not restart
  the document. Longer outages fall through to the job's durable backoff, and the
  document stays `PENDING`.
- Embeddings are written in the **same transaction** as their chunks, and a chunk
  row without a vector is unrepresentable (`NOT NULL`, migration 0004). `READY`
  is written only after that transaction verifies the index.
- Each row records its embedding space (`embedding_model`,
  `embedding_dimensions`), `embedding_input_sha256`, `embedded_at`, and the
  chunker version, so "which chunks need re-embedding" is a query rather than a
  guess (`npm run worker:index-status`).

**Cost control:** deduplication by `(workspace_id, content_sha256)` means an
identical re-upload is never re-embedded; identical inputs within a document are
embedded once; and vectors already stored for an identical input in the same
workspace and space are reused. Details: [embeddings.md](../database/embeddings.md).

## 5. Retrieval side

```
query ─▶ normalize (NFKC, control chars, whitespace)
  ├─▶ embed_query ─────────────────▶ dense:   HNSW, cosine, top N          ─┐
  └─▶ plainto_tsquery, & → | ──────▶ lexical: GIN, coverage score, top N   ─┤
                                                               │
                        Reciprocal Rank Fusion  Σ 1/(60+rank)  ◀┘
                                    │
                        optional rerank (no-op default)
                                    │
                                 top K
```

Dense and lexical fail in **uncorrelated** ways: dense handles paraphrase and
misses rare tokens (error codes, SKUs, surnames, versions); lexical does the
reverse. A knowledge platform faces both.

RRF fuses by **rank, not score**. Cosine similarity and `ts_rank_cd` are not
commensurable and have query-dependent distributions, so any weighted sum needs
per-corpus normalisation that nobody re-tunes. RRF needs none and degrades
gracefully when one retriever returns nothing. `k = 60` follows the original
paper and is a constant with a citation, not user-facing configuration.

Both queries carry `workspace_id` **inside** the query. Filtering after
retrieval would fetch another tenant's most relevant content first. Retrievers
return ids and scores; text is loaded afterwards by a query that re-applies the
same predicates. Details, and the evaluation that chose the lexical ranking and
the fusion, in [ADR-0021](../decisions/0021-hybrid-search-baseline.md) and
[retrieval.md](../benchmarks/retrieval.md).

**The known risk** is HNSW recall under a selective `workspace_id` filter.
pgvector's iterative index scan is the mitigation, and `hnsw.ef_search` is tuned
against measured recall, not guessed. This is the single most important
measurement in the system.

## 6. Generation and citation binding

```
question ─▶ record turn (question + PENDING answer, one transaction)
         ─▶ hybrid retrieval, top 12 ─▶ rerank (pass-through), top 8
         ─▶ no sources? answer "no evidence" WITHOUT calling the model
         ─▶ assign handles S1..Sn  (per request, server-side map)
         ─▶ construct context under a token budget
         ─▶ LLM stream ─▶ provisional deltas to the client
         ─▶ on completion: extract handles, RESOLVE, drop unresolvable
         ─▶ citation metadata read FROM THE DATABASE record of the chunk
         ─▶ PENDING answer → complete | partial | failed, exactly once
```

Implemented by `orbit.application.answering.answer_question` (the use case),
`orbit.domain.answering` (context and resolution, pure), and
`orbit.application.answering.prompt` (the versioned prompt). Decisions:
[ADR-0022](../decisions/0022-grounded-question-answering.md).

### Context construction

The budget is computed, not assumed: `min(ORBIT_ANSWER_MAX_CONTEXT_TOKENS,
context_window − prompt − reserved output − safety margin)`. Chunks are added in
rank order; one that does not fit is skipped (never truncated), and a smaller
one after it may still fit. At most `ORBIT_ANSWER_MAX_SOURCES`. A document is
never sent whole.

Overlapping chunks (ADR-0013 uses 64-token overlap) are **deduplicated** --
two chunks of one version overlapping by more than half the shorter, or
identical text in two documents -- before filling the budget, so the model is not shown
the same passage twice — which wastes budget and biases the answer toward
whatever happened to be duplicated.

Each chunk enters the prompt with its handle and its heading path, so the model
can attribute precisely.

### Citations are resolved, never parsed

Every extracted handle is looked up in the request-scoped map. **Anything that
does not resolve is discarded**, counted, logged with the `request_id`, and
exposed as a metric. Citation metadata — document, title, page, offsets,
snippet — is read from the chunk's database record.

The model therefore cannot cite a document that was not retrieved, a page that
does not exist, or text it invented. Handles are short ASCII tokens
deliberately: UUIDs would raise the transcription error rate and therefore the
discard rate.

**A rising discard rate is an alert.** It means the model, the prompt, or the
handle format has regressed.

### Grounding

The prompt instructs the model to use the supplied context, avoid unsupported
claims, begin with `[INSUFFICIENT_EVIDENCE]` when the sources do not answer the
question, cite only ids present in the request, distinguish what sources state
from what it infers, answer the question actually asked, and treat source text
as data rather than instructions. Conversation history is included with its
citation markers stripped, because handles are per request. Every answer is
classified `grounded`, `uncited`, `insufficient_evidence`, or `no_evidence`. An answer with **no citations is returned as
such** and rendered as visibly weaker by the frontend. Fabricating a confident
uncited answer is the failure mode this system is built to avoid; hiding the
distinction in the UI would undo the work.

## 7. Failure behaviour

| Failure | Ingestion | Chat (ADR-0022) |
|---|---|---|
| Provider 5xx | Retry with backoff; document stays `PENDING` | Up to 2 retries (a stream only before its first token), then 503 `GENERATION_FAILED` |
| Rate limit (429) | Retry honouring `Retry-After`; per-batch | Waited on up to 4 s, else 503 `GENERATION_RATE_LIMITED` + `Retry-After` |
| Timeout | Retry, bounded | 60 s wall-clock deadline; 503 `GENERATION_TIMEOUT`, text so far kept as `partial` |
| Circuit open | Jobs park in `PENDING` rather than burning retries | Fail fast (`GENERATION_FAILED`), no queueing behind a dead provider |
| Malformed output | n/a | Unparseable or empty: `GENERATION_FAILED`. Bad handles: dropped and counted, answer kept |
| Stream dropped mid-answer | n/a | `error` event; text so far kept as `partial` |
| Retrieval dependency down | n/a | 503 `RETRIEVAL_FAILED`; the model is not called |
| Nothing retrieved | n/a | `no_evidence` answer; the model is not called |
| Client disconnects | n/a | Generation cancelled; `partial` (or `failed`/`ANSWER_CANCELLED`) recorded |
| Answer cannot be saved | n/a | `CONVERSATION_UNAVAILABLE`; row stays `PENDING`, closed as abandoned by the next turn |
| Rejected key / unknown model | Startup fails validation | `ConfigurationError` → 500, logged; never retried |

An outage never marks a document `FAILED` — the document is fine, the provider
is not.

## 8. Changing provider or model

**Changing the embedding model is never silent.** Dense search filters on the
active embedding space, so vectors from a previous model are excluded rather
than mis-ranked.

- **Same width:** a re-index, not a migration. `npm run worker:reindex` moves
  stale chunks into the active space in place, resumably, without changing
  chunk ids, citations, or `READY` status.
- **Different width:** an expand/contract migration (new column, dual write,
  backfill, switch, drop). A width mismatch without the migration is refused:
  readiness reports `embedding_schema` down and workers will not embed.

The full procedure, including rolling deploys and the fake-to-real switch, is in
[embeddings.md](../database/embeddings.md#6-when-the-embedding-model-changes).

Changing the **LLM** is genuinely a configuration change: no stored state depends
on it. Prompts are versioned so an answer-quality regression after a model swap
can be attributed.

## 9. What is deliberately not built

- **No agent framework or tool-calling loop.** ORBIT answers from retrieved
  documents. Multi-step agents add latency, cost, and failure modes for no
  current requirement.
- **No fine-tuning.** Retrieval quality is the bottleneck, not model knowledge.
- **No LangChain / LlamaIndex.** The needed surface is three methods; adopting a
  framework means adopting its control flow and release cadence for the whole
  retrieval and chat path (ADR-0007).
- **No cross-encoder reranking by default.** The port exists; the default is a
  no-op. Reranking is added when benchmarks show fusion alone is insufficient,
  so its benefit can be measured against a known baseline.
- **No query rewriting or HyDE.** Both are real improvements and both are
  measurable additions later; neither is justified before there is a baseline.
