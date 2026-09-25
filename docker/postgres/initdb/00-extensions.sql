-- Extensions are created here rather than in an Alembic migration because
-- CREATE EXTENSION requires superuser, which the application role must not have.
-- Production provisioning performs the equivalent step out-of-band; see
-- docs/operations/provisioning.md.

CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector: embedding storage + ANN index
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- trigram similarity for fuzzy title/filename search
CREATE EXTENSION IF NOT EXISTS "uuid-ossp"; -- server-side UUID generation for defaults
