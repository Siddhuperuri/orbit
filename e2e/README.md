# End-to-end tests (Playwright)

34 tests that drive the **real stack** in a browser: a production build of the
Next app, the real FastAPI service, and a real Celery worker, against the
PostgreSQL, pgvector, Redis, and MinIO from `npm run infra:up`.

Nothing between the browser and the database is a double. The one substituted
component is the AI provider, which is ORBIT's own `fake` provider (ADR-0007):
deterministic, offline, and a supported operating mode rather than a test stub.
That is what lets these tests assert exact retrieval and citation results, and
it is why **no test here can depend on a real API key** — one present in your
`.env` is deliberately not forwarded to the stack under test.

## Running

```bash
npm run infra:up && npm run e2e:install && npm run e2e:test
```

`e2e:install` is needed once, and after a Playwright upgrade.

## What it runs against

The suite starts its own stack on its own ports, so `npm run dev:api` and
`npm run dev:web` can stay up while it runs:

| | dev | e2e |
|---|---|---|
| API | 8000 | 8100 |
| Web | 3000 | 3100 |
| Database | `orbit` | `orbit_e2e` |
| Redis databases | 0–2 | 12–14 |
| Next build dir | `.next` | `.next-e2e` |

`global-setup.ts` **drops and re-migrates** `orbit_e2e` before each run, so every
run proves the migrations apply from empty and no run inherits the last one's
rows. `provision_db.py` refuses any database whose name does not contain `e2e`,
because a typo in an environment variable must not be able to aim that at
`orbit`.

Credentials come from the repository's `.env` — the same file compose reads.
Nothing is hardcoded and nothing is read from a real deployment.

## Layout

| File | Covers |
|---|---|
| `tests/journey.spec.ts` | register → login → workspace → upload → process → search → open → ask → citation → log out |
| `tests/auth.spec.ts` | sign-in and sign-up failures, account enumeration, open redirect |
| `tests/uploads.spec.ts` | unsupported type, lying extension, empty, truncated, oversize |
| `tests/security.spec.ts` | IDOR, cross-tenant, anonymous access, session revocation and replay, CSRF, malformed input, rate limiting |
| `tests/reliability.spec.ts` | readiness, duplicate jobs, processing failure, ungrounded answers |

`fixtures/pdf.ts` builds genuine PDFs in-process — a TypeScript sibling of
`backend/tests/fixtures/pdf.py`, and for the same reason: the document's words
belong in the spec next to the search that has to find them, not in a committed
binary.

## Determinism

- The worker runs `--pool=solo` (prefork needs `fork(2)`), so it processes one
  document at a time and the suite runs `workers: 1`. Correctness over minutes.
- Retrieval is lexical overlap under the fake provider, so each fixture section
  owns a distinctive term and the spec searches for that term.
- Fixed viewport, `UTC`, and `en-US`, so layout and formatting do not vary by
  machine.
- Waits are on state (`Ready`, `Sources`), never on a sleep. Timeouts are
  ceilings, not delays.
- `retries: 0` locally. A test that only passes on the second attempt is lying.

## Known gap

The worker's Playwright `webServer` gate polls the API's `/healthz`, which proves
the worker process *started*, not that it registered with the broker — Celery
serves no HTTP port and Playwright can only gate on a URL. That gap is closed
where it matters: the first upload waits for its document to leave `pending`,
with a timeout long enough to cover worker cold start, so a silent worker fails
that wait loudly rather than passing quietly.
