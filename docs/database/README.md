# Database documentation

- **[schema.md](schema.md)** — the 15-table model, what was deliberately *not*
  built, deletion strategy, ownership rules, workspace boundaries, and
  concurrency. Written.
- **[indexes.md](indexes.md)** — all 29 indexes, the query each serves, and what
  is deliberately absent. Written.
- `embeddings.md` — pgvector dimensioning, and the re-embed/re-index procedure required when the embedding model changes (M4)
- `migrations.md` — expand/contract discipline for zero-downtime rolling deploys (M1)

Extensions (`vector`, `pg_trgm`, `uuid-ossp`) are created by
`docker/postgres/initdb/` locally, and out-of-band in production, because
`CREATE EXTENSION` requires privileges the application role must not hold.
