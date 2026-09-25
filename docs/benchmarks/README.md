# Benchmarks

Measurements, with the method recorded alongside each number so results can be
reproduced and compared over time. Unmeasured performance claims do not belong
here or anywhere else in this repository.

- [`vector-search.md`](vector-search.md) — HNSW build cost, recall@k, and
  latency of the dense search query by workspace size, `ef_search`, and iterative
  scan mode (synthetic vectors; `npm run bench:vector`)
- [`retrieval.md`](retrieval.md) — P@k, R@k, and MRR of lexical, semantic, and
  fused retrieval on a labelled 42-query set, including tenant-isolation attacks
  (`npm run eval:retrieval`). Last run used the fake embedding provider; the
  real-provider run is outstanding
- `ingestion.md` — end-to-end document processing latency by size and format (M4)
- `api.md` — endpoint latency distributions under concurrency (M8)

Retrieval quality must be measured against a real embedding provider; the
deterministic fake provider is not semantically meaningful.
