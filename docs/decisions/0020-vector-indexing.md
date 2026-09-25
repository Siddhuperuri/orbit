# 0020 — Vector indexing in pgvector: self-describing embedding spaces, verified READY, in-place re-index

- **Status:** Accepted — amends [0007](0007-ai-provider-ports.md)
- **Date:** 2026-09-17

## Context

M4 embedded chunks and stored the vectors, but the index was not yet something
retrieval could trust:

- `chunks.embedding` was nullable, and a vector was described only by a model
  name — not its width, not when it was made, not what text it was made from.
- Nothing prevented a vector from another model being compared with a query:
  the space a vector belongs to was not part of any query.
- The OpenAI adapter hardcoded its endpoint and batch size, and had no retry of
  its own: one 429 on batch 7 of 12 failed the whole attempt, and the next
  attempt paid for batches 1–6 again.
- `READY` followed a successful insert, but nothing *verified* the index before
  it was written.
- A model change was described (ADR-0007) as a data migration with no tooling,
  and nothing measured the HNSW index under ORBIT's defining constraint, the
  tenant filter.

The brief requires PostgreSQL + pgvector unless analysis proves another vector
store necessary.

## Decision

### 1. Stay in PostgreSQL

The tempting alternative is a dedicated vector database (Qdrant, Weaviate,
Pinecone, OpenSearch k-NN). What it would buy, and what it would cost:

| A dedicated store would give | ORBIT's position |
|---|---|
| Filtered ANN designed in (payload indexes, filterable HNSW) | Measured: pgvector's iterative scan gives recall@10 = 1.000 under the workspace filter, and the planner uses an exact B-tree scan for small tenants ([benchmark](../benchmarks/vector-search.md)) |
| Horizontal sharding | Not needed while the index fits in one host's memory: ≈ 8 GB per million chunks at 1,536 dimensions |
| Built-in quantization | `halfvec` / binary quantization exist in pgvector; not yet needed |

| A dedicated store would cost | |
|---|---|
| Consistency | Chunks, citations, tenancy, and `READY` live in Postgres. A second store needs dual writes and a reconciliation job for every partial failure; today one transaction writes the chunks, their vectors, and `READY`. |
| Tenant isolation | The workspace predicate would have to be re-implemented, and kept correct, in a second query language. |
| Deletion | Soft delete, version supersession, and workspace purge would each need a second, eventually consistent path. |
| Operations | Another stateful service to back up, restore to the same point in time as Postgres, secure, and monitor. |

At 100,000 chunks the measured p95 is under 5 ms of server time for a 50,000-chunk
tenant at perfect recall. **Revisit** when, on real embeddings at the real
corpus size and with pgvector tuned, p95 or recall misses target, or the index
no longer fits in memory on the largest reasonable single host.

### 2. The embedding space is identity, stored on every vector

`EmbeddingSpace(model, dimensions)` is a domain value. Each chunk row records
`embedding_model`, `embedding_dimensions` (with a `CHECK` that it equals
`vector_dims(embedding)`), `embedding_input_sha256`, and `embedded_at`. The
provider port declares its space; dense search passes the space explicitly and
filters on it, so vectors from any other space are **excluded, never compared**.

### 3. A chunk row is an indexed chunk; READY is verified

The vector and its metadata are `NOT NULL`. The index transaction inserts the
chunks, counts the version's chunks in the active space, and only if the count
matches writes `READY` — in the same transaction, as the last write.

### 4. Nothing about the vendor is hardcoded

API key (a secret type), base URL, model, and width are configuration; width has
no default. The fake provider has its own fixed model id and ignores
`ORBIT_EMBEDDING_MODEL`, so fake vectors can never be mistaken for a real
model's output after a switch. Mismatched width is refused: `/readyz` reports
`embedding_schema` down, and the worker checks the catalog before any provider
call.

### 5. Two layers of retry

A request is retried in-process (bounded, jittered, honouring `Retry-After` up to
a cap), so a brief rate limit costs one batch rather than the document. Longer
outages, or a `Retry-After` beyond the cap, fall through to the job's durable
backoff (ADR-0019) and release the worker. Credentials, quota exhaustion, an
unknown model, or a wrong width are configuration defects and are not retried.
A malformed response — wrong count, NaN, infinity, zero vector — is retried as
a provider fault.

### 6. Reuse by input hash, within a workspace and a space

Before calling the provider, the embed stage collapses identical inputs and
looks up vectors already stored for the same `embedding_input_sha256` in the
same workspace and space. Reprocessing an indexed document costs zero provider
calls. Reuse never crosses a tenant: how quickly and cheaply a document indexes
must not reveal what another tenant holds.

### 7. Index and query

- HNSW, `vector_cosine_ops`, `m = 16`, `ef_construction = 64` (unchanged).
- `hnsw.iterative_scan = relaxed_order`, with the small result re-sorted by
  distance. Measured: without it, recall under the tenant filter dropped to
  0.942, with some queries returning none of their true neighbours.
- `hnsw.ef_search = 100` by default. Measured: 200 made the planner switch to a
  sequential scan, 65× slower, with no recall to gain.
- All GUCs are set transaction-locally.

### 8. Model changes

- **Same width:** an in-place re-index (`reindex-embeddings`): resumable,
  idempotent, conditional on each chunk's input hash, leaves chunk ids,
  citations, and `READY` untouched. Dense results for not-yet-migrated documents
  are missing during the run.
- **Different width:** an expand/contract column migration. Not automated.
- Procedure and failure modes: [embeddings.md](../database/embeddings.md).

## Alternatives considered

**A dedicated vector database.** Rejected for now; see decision 1.

**A `chunk_embeddings` table keyed by space, with a partial HNSW index per
space.** Allows two spaces to be live at once, so a model change needs no
degraded window and no migration. Rejected for now: a partial index on
`embedding_model = '…'` is matched by the planner only when the query contains
that literal, not a bound parameter, so every search would need
literal-inlined SQL; and nothing needs two live spaces yet. Adopt it when
per-workspace models or A/B evaluation of models are required.

**An untyped `vector` column holding any width, with per-width expression
indexes.** Same planner limitation, and loses the column type as a guard.

**Retries only at the job level (M4's behaviour).** Rejected: a single 429
discards every batch already embedded for the document.

**A circuit breaker in the decorator.** Rejected: the worker builds a container
per task, so breaker state would not outlive one document. The job backoff's
jitter prevents a synchronized retry storm; a shared breaker (in Redis) can be
added if an outage shows it is needed.

**Reuse across workspaces.** Rejected as a side channel (decision 6).

**Reuse across versions of the same document.** Not possible today: uploading a
version deletes the previous version's chunks immediately (M3). Deferring that
deletion to the new version's index transaction would enable it. Left as a
separate decision because it changes version-swap semantics.

**Inner product (`vector_ip_ops`).** Equivalent ranking for unit vectors and
slightly cheaper, but wrong for a provider that does not normalise. Not measured
as a bottleneck.

**`halfvec` indexing.** Would roughly halve index memory. Deferred until memory
is the measured constraint, with a recall check on real embeddings.

**Per-workspace partial indexes or partitioning by workspace.** The planner
already uses an exact B-tree scan for small tenants; per-tenant indexes would
multiply DDL with tenant count for no measured gain.

## Consequences

- A `READY` version is always fully indexed in a known space; a chunk without a
  vector cannot exist.
- A model change is visible (`index-status`), safe (never mis-ranked), and
  repairable in place at the same width.
- The embed stage costs one extra catalog query and one reuse lookup per job,
  and renews the lease after every provider request.
- Changing `ORBIT_EMBEDDING_DIMENSIONS` without a migration fails documents as
  configuration defects; they need reprocessing once fixed.
- The HNSW index needs memory, ≈ 8 GB per million chunks, and a parallel
  rebuild needs a larger `/dev/shm` than Docker's default.
- The benchmark uses synthetic vectors. It must be re-run on real embeddings
  before production sizing, and whenever `ef_search` is changed.
