# Testing

| Layer | Location | Tests | Requires |
|---|---|---|---|
| Unit | `backend/tests/unit` | 1,300+ | nothing |
| API | `backend/tests/api` | 204 | nothing (in-process ASGI client) |
| Integration | `backend/tests/integration` | 279 | Postgres, Redis, MinIO via `npm run infra:up` |
| Component | `web/**/*.test.tsx` | 404 (with unit) | nothing (jsdom) |
| E2E | `e2e/tests` | 34 | full stack — see [e2e/README](../../e2e/README.md) |

Backend layers are separated by the pytest markers `integration` and `e2e`, so
`npm run backend:test` runs the infrastructure-free subset.

## Commands

```bash
npm run backend:test              # unit + API, no infrastructure
npm run backend:test:integration  # needs npm run infra:up (see below)
npm run backend:test:coverage     # unit + API with a coverage report
npm run web:test                  # vitest
npm run web:test:coverage         # vitest with v8 coverage
npm run e2e:test                  # needs npm run infra:up
```

### Configuring the integration suite

Integration tests **skip themselves** rather than fail when they are not
configured, so an unconfigured run looks like a pass with a lot of skips. Read
the skip count: 279 tests should run. `ORBIT_TEST_DATABASE_URL` alone gets 232 of
them; the object-storage and Redis tests need the rest.

```bash
export ORBIT_TEST_DATABASE_URL="postgresql+asyncpg://orbit:$PASSWORD@127.0.0.1:5432/orbit_test"
export ORBIT_REDIS_URL="redis://127.0.0.1:6379/9"
export ORBIT_S3_ENDPOINT_URL="http://127.0.0.1:9000"
export ORBIT_S3_BUCKET="orbit-documents"
export ORBIT_S3_ACCESS_KEY_ID="$MINIO_ROOT_USER"
export ORBIT_S3_SECRET_ACCESS_KEY="$MINIO_ROOT_PASSWORD"
npm run backend:test:integration
```

Use `127.0.0.1`, not `localhost`: where the containers are reached through a
port forward, `localhost` can resolve to `::1` first and hang until it times
out, which surfaces as a connection error far from its cause.

## Where a test belongs

- **Unit** — a rule that can be stated without a database: chunking, ranking and
  fusion, query parsing, validation, token and password handling, the access
  matrix, context construction. Fakes live in `tests/unit/fakes/`.
- **API** — the router, its dependencies, status codes, and the error envelope,
  through the in-process ASGI client. No socket, no container.
- **Integration** — anything whose behaviour *is* the database or the object
  store: SQL repositories, keyset pagination, query plans, transaction
  boundaries, optimistic concurrency, pgvector index behaviour, migrations, the
  S3 adapter against real MinIO. These are why the repository modules read low
  in a unit-only coverage report — that code is exercised here.
- **Component** — a React component's rendered behaviour: forms, validation
  messages, loading and error states, citations, search results.
- **E2E** — a journey a person takes across more than one service, and the
  security boundaries as they are reachable over the wire.

## Standing rules

- Failure cases are mandatory. A resource with only happy-path coverage is
  incomplete under the Definition of Done.
- Every tenant-scoped resource requires an explicit cross-tenant denial test.
  A cross-tenant read answers **404, not 403** — a 403 confirms the id exists
  and is itself the leak.
- The suite runs with **no AI credentials and no network** (ADR-0007). The
  `fake` provider is deterministic and is a supported operating mode, not a
  stub. A test that would need a real key does not get written.
- Tests are never deleted or skipped to make a build pass.
- Waits are on state, never on a sleep. A timeout is a ceiling, not a delay.

## Coverage

`npm run backend:test:coverage` writes `backend/.coverage-html`, and
`npm run web:test:coverage` writes `web/coverage`.

Read the backend number with care: the unit+API subset reports ~85% of
`src/orbit`, but the SQL repository modules sit between 19% and 43% in that run
because their tests are the integration suite, which the subset deselects. The
honest figure for a module in `infrastructure/db/repositories/` comes from
`npm run backend:test:coverage:all` with infrastructure up.
