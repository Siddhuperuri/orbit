# Retrieval quality

Precision, recall, and MRR of ORBIT's lexical, semantic, and hybrid retrieval
on a labelled evaluation set, through the production SQL. Raw per-query results:
[`data/retrieval-2026-09-18-fake-provider.json`](data/retrieval-2026-09-18-fake-provider.json).

**Reproduce:** `npm run eval:retrieval` (against a `*_test` or `*_bench`
database). To measure real semantic retrieval, run it with
`ORBIT_AI_PROVIDER=openai` and the `ORBIT_OPENAI_*` / `ORBIT_EMBEDDING_MODEL`
settings. That run has **not** been done yet (see "What this run cannot tell you").

---

## The evaluation set

`backend/benchmarks/retrieval/`: 16 short policy and runbook documents for the
searched tenant (`acme`), and 2 documents for a second tenant (`globex`):
confidential board minutes, plus a verbatim copy of one acme policy. Each is
ingested through the production parser, normaliser, chunker, and embed stage
(35 chunks for acme).

42 queries in 7 categories, relevance judged per document:

| Category | n | What it tests |
|---|---:|---|
| `keyword` | 7 | The answer's own words. Lexical should win. |
| `semantic` | 7 | A natural question in the asker's words, partial overlap. |
| `both` | 6 | A rare identifier plus a question. |
| `paraphrase` | 7 | **Keyword fails, semantic should succeed.** No stemmed term shared with the answer; *verified* by the harness with PostgreSQL's parser. |
| `identifier` | 7 | **Semantic fails, keyword should succeed.** An error code, SKU, version, surname, address, or product name that the answer contains verbatim; *verified*. |
| `distractor` | 6 | Close neighbours exist (sick pay vs. parental leave vs. holiday; P1 support vs. SEV1 incidents; meals vs. hotels). |
| `isolation` | 2 | Asks for the other tenant's confidential content, one query verbatim. Correct answer: nothing from that tenant. |

The harness refuses to run if any `paraphrase` or `identifier` claim is false.

## Method

| | |
|---|---|
| Date | 2026-09-18 |
| Database | PostgreSQL 17.11, pgvector 0.8.6 (same host as [vector-search.md](vector-search.md)) |
| Embedding provider | **`fake`** (`orbit-fake-embedding-v1@1536`): hashed word counts. No API key is configured on this machine. |
| Candidates per retriever | 50 |
| Metrics | Document level. Chunks are collapsed to their document in rank order. P@1, P@3, R@5, R@10, and MRR@10 over the 40 scored queries (isolation excluded). |
| Consistency check | The production `HybridSearch` use case was run for every query and ranked **identically** to the harness's RRF k=60 fusion. |

Configurations: three lexical variants, semantic alone, and three fusions of the
production lexical and semantic lists.

- `lexical`: the production full-text retriever, OR over terms, ranked by coverage, then proximity, then frequency.
- `lexical_ts_rank_cd_only`: OR over terms, ranked by `ts_rank_cd` alone.
- `lexical_all_terms`: AND over terms (`plainto_tsquery` as is).
- `semantic`: pgvector cosine.
- `hybrid_rrf_k60`: **production**, Reciprocal Rank Fusion with k = 60.
- `hybrid_rrf_k10`: RRF with k = 10.
- `hybrid_min_max_50_50`: each list's scores min-max normalised to [0, 1], equal-weight sum.

## Results

### Overall (40 scored queries)

| Configuration | P@1 | P@3 | R@5 | R@10 | MRR@10 |
|---|---:|---:|---:|---:|---:|
| lexical | **0.725** | 0.250 | 0.775 | 0.787 | 0.750 |
| lexical_ts_rank_cd_only | 0.700 | 0.250 | 0.775 | 0.787 | 0.738 |
| lexical_all_terms | 0.375 | 0.125 | 0.375 | 0.375 | 0.375 |
| semantic (fake) | 0.625 | 0.250 | 0.825 | 0.900 | 0.701 |
| **hybrid_rrf_k60** | 0.675 | **0.267** | 0.825 | **0.925** | 0.753 |
| hybrid_rrf_k10 | 0.675 | **0.267** | 0.825 | **0.925** | 0.745 |
| hybrid_min_max_50_50 | **0.725** | 0.250 | **0.838** | **0.925** | **0.771** |

P@3 is bounded by the number of relevant documents (usually 1, so at most 0.333).

### MRR@10 by category

| Configuration | keyword | identifier | both | distractor | semantic | paraphrase |
|---|---:|---:|---:|---:|---:|---:|
| lexical | 1.000 | 1.000 | 1.000 | 1.000 | 0.571 | 0.000 |
| lexical_ts_rank_cd_only | 1.000 | 1.000 | 1.000 | 1.000 | 0.500 | 0.000 |
| lexical_all_terms | 0.857 | 1.000 | 0.000 | 0.333 | 0.000 | 0.000 |
| semantic (fake) | 1.000 | 1.000 | 0.889 | 0.806 | 0.387 | 0.166 |
| hybrid_rrf_k60 | 1.000 | 1.000 | 1.000 | 1.000 | 0.476 | 0.115 |
| hybrid_rrf_k10 | 1.000 | 1.000 | 1.000 | 1.000 | 0.429 | 0.115 |
| hybrid_min_max_50_50 | 1.000 | 1.000 | 1.000 | 1.000 | 0.571 | 0.118 |

### Safety

| Measure | Every configuration, and the production use case |
|---|---|
| Results from the other tenant, including for the verbatim copy of its confidential text | **0** |
| Distractor ranked above the answer (6 queries) | 0 |

## Findings

1. **AND over terms is the wrong default.** Requiring every query term halved
   MRR (0.375 against 0.750) and scored zero on the `both` and `semantic`
   categories, because natural questions contain words their answers do not.
   The full-text retriever uses OR (`ORBIT_SEARCH_LEXICAL_MATCH=any`).

2. **Plain `ts_rank_cd` is a weak ranking under OR.** Every single matching term
   counts as its own "cover", so it reduces to counting occurrences. Ranking by
   distinct terms covered, then proximity of all terms, then frequency lifted
   MRR from 0.738 to 0.750 overall and from 0.500 to 0.571 on the `semantic`
   category. It also fixed a ranking an integration test pins: a passage that
   answers the question beating one that merely mentions its words. The gain
   is small on 40 queries; the ordering rule is what matters.

3. **Hybrid raised recall and did not lower MRR, even with a fake semantic
   side.** R@10 went from 0.787 (lexical) to 0.925 (RRF); MRR 0.750 to 0.753.
   Both retrievers contributed to ranking. On sem-2 and sem-3, RRF placed the
   answer higher than either retriever did alone.

4. **Hybrid lowered P@1 (0.725 to 0.675), and the cause is the fake provider.**
   The fake embeds unstemmed words including stop words ("the", "for", "my"), so
   its "semantic" list is noisy lexical matching. On sem-1 and sem-5, lexical
   ranked the answer first and the fake's list pulled it to 3rd and 2nd. A real
   embedding model is expected to behave differently. That expectation is
   **not measured**.

5. **Paraphrase queries are unsolved in this run, as expected.** Lexical scored
   0.000 by construction (verified: no shared terms). The fake's 0.166 comes
   from shared stop words, not meaning. This is the category a real embedding
   model exists to win, and the one this run cannot evaluate.

6. **RRF stays the production fusion; min-max scored slightly higher here.**
   Min-max reached 0.771 MRR against RRF's 0.753. The gap is about one query's
   worth of reciprocal rank on 40 queries, and it was earned against a fake
   semantic score distribution. Min-max depends on each retriever's score
   distribution, which will change completely with a real model; RRF depends
   only on ranks (ADR-0005). Re-evaluate with a real provider before
   reconsidering. k = 10 against k = 60 made no difference beyond noise.

7. **Isolation held under direct attack.** Zero results from the other tenant
   in every configuration, including a query that was that tenant's
   confidential sentence verbatim. That query's text is also an exact copy of
   no acme document, so the searched tenant correctly returned only unrelated
   acme passages.

8. **There is no relevance floor.** Search returns the best available passages
   even when none is relevant (the isolation queries returned unrelated acme
   policies). That is correct for a ranking API. Answer generation (M6) must
   decide when sources are insufficient rather than assume the top result is
   an answer.

## What this run cannot tell you

- **Anything about semantic retrieval quality.** The `semantic` rows measure the
  fake provider, which is lexical underneath. The paraphrase category, the
  semantic-vs-hybrid comparison, and the RRF-vs-min-max choice all need the
  real-provider run.
- **Behaviour at scale.** 16 documents and 35 chunks is enough to test ranking
  logic and isolation, not enough to rank retrievers confidently. Latency at
  scale is in [vector-search.md](vector-search.md).
- **Statistical confidence.** 40 queries, one run. Differences of about 0.02 MRR
  are within one query's outcome.
