# 0005 — Hybrid retrieval inside PostgreSQL, fused with Reciprocal Rank Fusion

- **Status:** Accepted — implemented and measured in [0021](0021-hybrid-search-baseline.md)
- **Date:** 2026-09-09

## Context

Answer quality in a RAG system is bounded by retrieval. If the correct chunk is
not in the candidate set, no amount of prompt engineering recovers it.

Dense (embedding) retrieval and lexical (keyword) retrieval fail in different,
largely uncorrelated ways:

- **Dense** handles paraphrase and synonymy, and is weak on rare tokens — error
  codes, product SKUs, surnames, version numbers, acronyms. Precisely the terms
  users search for in their own documents.
- **Lexical** handles exact and rare tokens well, and fails completely when the
  user's wording differs from the document's.

A knowledge platform is exposed to both. Choosing one means accepting a known
class of unanswerable questions.

## Decision

**Hybrid retrieval executed entirely in PostgreSQL, fused with Reciprocal Rank
Fusion.**

- **Dense:** `pgvector` with an **HNSW** index over cosine distance.
- **Lexical:** a stored `tsvector` column with a **GIN** index, ranked with
  `ts_rank_cd`.
- **Fusion:** RRF, `score(d) = Σ 1 / (k + rank_i(d))` with `k = 60`.

RRF combines rankings by **position, not by score**. This matters: cosine
similarity and `ts_rank_cd` are not commensurable, live on different scales, and
have query-dependent distributions. Any weighted sum of the two requires
normalisation that has to be re-tuned per corpus. RRF needs no normalisation, no
per-corpus tuning, and degrades gracefully when one retriever returns nothing.

Both retrievers apply the workspace predicate from ADR-0004 *inside* the query.

## Alternatives considered

**Dense only.** Rejected: fails on exactly the rare-token queries users type
when searching their own material.

**Lexical only.** Rejected: no semantic recall, which is the product premise.

**Weighted score fusion** (`α · cosine + (1 − α) · bm25`). Rejected: requires
score normalisation, and α is corpus-dependent. It becomes a tuning parameter
nobody re-tunes, and it silently rots as the corpus changes.

**A dedicated search engine** (Elasticsearch, OpenSearch, Vespa, Weaviate,
Qdrant). Better retrieval features and better scaling headroom. Rejected: a
second stateful system to run, back up, secure, keep consistent with Postgres,
and reconcile after partial failure. Postgres is already the source of truth and
already required. This is precisely the "infrastructure added to make the
architecture diagram look impressive" that the brief forbids. Revisit when
measured retrieval latency or corpus size makes it necessary — the
`SearchRepository` port exists so that this is a replacement, not a rewrite.

**IVFFlat instead of HNSW.** Rejected: IVFFlat requires training on
representative data and degrades as the corpus grows away from that training
set. HNSW builds incrementally, which suits a system where documents arrive
continuously. HNSW costs more memory and more build time; that trade is
accepted.

## Consequences

- **Index/filter interaction is the main risk.** HNSW with a highly selective
  pre-filter can over-scan or under-return. Recall against the workspace filter
  is measured, and results are recorded in `docs/benchmarks/retrieval.md`.
  `hnsw.ef_search` is tuned against that measurement rather than guessed.
- Two indexes must be maintained per chunk, so ingestion writes more and the
  `tsvector` is generated at write time, not query time.
- Full-text configuration (`english`) is stored per document to leave room for
  multilingual support without a rewrite.
- `k = 60` follows the original RRF paper and is deliberately *not* exposed as
  user-facing configuration; it is a constant with a citation, revisited only
  with benchmark evidence.
- Reranking is a separate, optional stage behind a `Reranker` port (default:
  no-op). Fusion produces the candidate set; reranking refines it. Keeping them
  separate means retrieval quality can be measured independently of reranker
  quality.
