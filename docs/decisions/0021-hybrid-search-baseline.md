# 0021 — Hybrid search baseline: coverage-ranked full-text + pgvector, fused by RRF

- **Status:** Accepted — implements [0005](0005-hybrid-retrieval-rrf.md)
- **Date:** 2026-09-18

## Context

ADR-0005 chose hybrid retrieval inside PostgreSQL with Reciprocal Rank Fusion.
M5 has to turn that into a search engine that is correct, secure, and
measurable, without building more than a baseline needs. Four questions were
left open:

1. How the full-text query is formed, and how its matches are ranked.
2. How lexical and vector candidates are combined, and whether score
   normalisation beats rank fusion.
3. How authorization is guaranteed when the candidate generator is a
   similarity search, which will happily rank another tenant's text first.
4. How to know whether any of it works.

## Decision

### Pipeline

```
SearchQuery ─ normalize (NFKC, strip control/format chars, collapse whitespace)
   ├─ lexical:  plainto_tsquery('english') with & rewritten to |  → GIN → coverage score   (top 50)
   └─ semantic: embed_query (concurrently) → HNSW, active space → cosine similarity       (top 50)
          ↓  both workspace-filtered inside their SQL
   RRF, k = 60  →  top K ids  →  load_results (workspace + visibility re-checked)  →  results
```

Each stage is a separate, separately tested unit: normalisation and fusion are
pure functions (`orbit.domain.retrieval`), the retrievers are repository
methods (`SearchRepository`), and the use case (`HybridSearch`) composes them.

### Lexical retrieval

- The query is parsed by `plainto_tsquery('english')`, the same parser and
  stemmer that generated `chunks.search_vector`, so users get no query syntax
  to get wrong. Its `&` operators become `|`: a chunk needs *some* of the
  terms. Measured: requiring all terms halved MRR (0.375 against 0.750).
- **Score** for a query of n distinct terms:
  `(terms matched + (cd(all terms) + cd(any term)) / 2) / n`, where `cd` is
  `ts_rank_cd(..., 32)` in [0, 1). Coverage first, then proximity of all the
  terms, then frequency. Plain `ts_rank_cd` under OR is a disguised occurrence
  count (each term is its own cover). It mis-ranked a pinned test case and
  scored lower in evaluation.

### Semantic retrieval

Cosine similarity over the HNSW index, restricted to the active embedding space
(ADR-0020), `hnsw.iterative_scan = relaxed_order`, `ef_search` 100.

### Fusion: Reciprocal Rank Fusion, k = 60

`score(chunk) = Σ over retrievers that returned it of 1 / (60 + rank)`. Ties
break on the best single rank, then chunk id, so results are deterministic.
Fusion uses positions, never raw scores, because `ts_rank_cd`-derived scores
and cosine similarity have unrelated, query-dependent distributions.

Evaluated alternatives on the same run (docs/benchmarks/retrieval.md): min-max
score normalisation with equal weights scored 0.771 MRR against RRF's 0.753,
and RRF with k = 10 scored 0.745. The min-max advantage is about one query in
40, and it was measured against the *fake* embedding provider's score
distribution, which a real model will replace entirely. RRF is kept until a
real-provider evaluation says otherwise.

### Results are structured, and say how they were found

Each result carries its document (id, title), version (id, number), chunk (id,
ordinal, text), source location (pages, heading path, character offsets), its
rank, the fused score, each retriever's rank and native score, and
`matched_by` (lexical, semantic, or both). The response names the retrievers
that actually ran. When the embedding provider is down, a hybrid request is
answered lexically with `retrievers = [lexical]` and
`degraded = "semantic_unavailable"`: never a lexical ranking labelled hybrid.

### Authorization in depth

1. The HTTP layer resolves membership for the path's workspace (a non-member
   gets 404), and the use case requires `search:query`.
2. **Both retrievers put `workspace_id`, current-READY-version, and
   not-deleted predicates inside their SQL.** Another tenant's chunk is never a
   candidate, however close its vector. Similarity is never computed across
   tenants and filtered afterwards.
3. Retrievers return ids and scores only. Text is materialised by a second
   query that re-applies the same predicates, and the use case drops (and logs
   as a defect) any row whose workspace differs from the caller's.
4. A `document_ids` filter is intersected with the workspace, so naming another
   tenant's document matches nothing.

Tested at unit, integration (real PostgreSQL), and API level, including an
attacker searching with another tenant's confidential sentence verbatim:
zero results through every retriever.

### Measured, not assumed

`backend/benchmarks/retrieval/` holds a 42-query labelled set in seven
categories. The harness verifies the set's own claims (paraphrases share no
stemmed term with their answers; identifiers occur in theirs), runs the
production SQL, checks that the production use case ranks identically, and
reports P@1, P@3, R@5, R@10, and MRR@10.

## Alternatives considered

**`websearch_to_tsquery` / user-facing query syntax.** Quotes and `-` operators
are useful to power users and a source of zero-result surprises for everyone
else. Deferred until someone asks for it.

**BM25 (via an extension or an external engine).** Better term weighting than
`ts_rank_cd`, but needs an extension ORBIT cannot assume on managed PostgreSQL
(ADR-0005 rejected the external engine). Coverage ranking closes the most
visible gap.

**Weighted score fusion (min-max, z-score).** Evaluated (above). Rejected for
now: it needs per-corpus tuning, and its only measured advantage came from a
fake score distribution.

**A cross-encoder reranker.** The port exists (ADR-0007); not added until a
real-provider evaluation shows fusion is the bottleneck.

**A relevance threshold.** Rejected for the search API: whether "the best
passages are not good enough" is a question for answer generation (M6), and a
threshold on fused ranks has no meaning. Measured consequence: queries with no
relevant document still return passages.

**Filtering results after retrieval** instead of inside SQL. Rejected: it
under-returns and, worse, computes similarity against another tenant's data.

## Consequences

- One search request: an embedding call concurrent with one lexical query,
  then one vector query and one materialising query.
- The lexical score counts matched terms per candidate row. That is cheap for
  chunk-sized documents; if a very common term makes the GIN match set large,
  it is the first thing to profile.
- Search is usable, and its isolation proven, with the fake provider. Its
  **semantic quality is unmeasured** until `npm run eval:retrieval` runs with a
  real provider. That run gates any claim about hybrid's benefit and any
  change of fusion method.
