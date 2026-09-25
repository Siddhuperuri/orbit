-- Dedicated database for the integration test suite. Kept separate from the
-- development database so that a destructive test run can never wipe local work.
SELECT 'CREATE DATABASE orbit_test'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'orbit_test')\gexec

\connect orbit_test
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
