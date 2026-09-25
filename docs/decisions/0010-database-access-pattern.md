# 0010 — Repositories with an explicit unit of work, and no lazy loading

- **Status:** Accepted
- **Date:** 2026-09-09

## Context

Section 4 of the engineering brief forbids database queries in route handlers,
and ADR-0004 requires that every tenant-scoped query carry an `AccessContext`.
Neither settles the harder questions: who owns the transaction, what crosses the
repository boundary, and how N+1 queries are prevented rather than discovered in
production.

There is also a structural constraint from ADR-0002. The API is asyncio; the
Celery worker is prefork and synchronous. The persistence layer has to serve
both without being written twice.

## Decision

### Repositories return domain objects for writes and projections for reads

A single "return ORM models everywhere" rule is wrong in both directions. Full
hand-mapping of every entity is boilerplate that pays for itself only where
behaviour exists; returning ORM models everywhere leaks SQLAlchemy — and lazy
loading — into `application` and `api`.

So the boundary is split by intent:

- **Command path (writes).** Repositories accept and return **domain entities**:
  plain, framework-free objects in `orbit.domain.models` that hold invariants.
  Mapping to ORM models happens inside `infrastructure`.
- **Query path (reads).** List and detail views return **purpose-built read
  models** — flat, immutable DTOs assembled by a single query with exactly the
  columns and joins that view needs.

Reads do not travel through the domain model. A document list needs a title, a
status, a page count, and a tag array; reconstructing full aggregates to render
it is wasted work and invites N+1.

This is CQRS-lite: separate paths, one database, one transaction model. It is
not event sourcing and does not introduce a read replica or a projection store.

### The use case owns the transaction, not the repository

Repositories never commit. A use case opens a unit of work, calls one or more
repositories, and commits once:

```python
async with self._uow() as uow:
    document = await uow.documents.create(ctx, ...)
    await uow.jobs.enqueue_record(ctx, document.id)
    await uow.commit()
```

A repository that commits makes multi-repository invariants impossible to
express, which is exactly what "create a document *and* its processing job"
requires.

### Lazy loading is disabled — `lazy="raise"` on every relationship

Every relationship is configured `lazy="raise"`. Accessing an unloaded
relationship raises immediately instead of silently emitting a query.

This is the single highest-value decision in this ADR. An N+1 in a document list
is invisible in development with three documents and a production incident with
three thousand. `lazy="raise"` converts a latent performance defect into a loud,
deterministic failure that surfaces in the first test that touches the path.
Loading is therefore always explicit at the query site (`selectinload`,
`joinedload`), where the reviewer can see it.

### Two session factories, one repository interface

`AsyncSession` for the API, `Session` for the worker. Repository *protocols* are
declared once in `orbit.domain.ports`; `infrastructure` provides an async and a
sync implementation of the small subset the worker actually needs. The worker
performs a handful of well-known operations, so this is a modest duplication,
not a parallel data layer.

### Every query is bounded

No repository method returns an unbounded collection. List methods take a
**keyset cursor** and a limit with a server-enforced maximum. `LIMIT`-less
queries are a production outage waiting for the row count to arrive.

## Alternatives considered

**Active Record / ORM models as the domain model.** Fewer moving parts, and a
legitimate choice for a small team. Rejected: it makes `application` depend on
SQLAlchemy, defeats the import contract from ADR-0001, and makes unit-testing
business rules require a database.

**SQLAlchemy imperative mapping** so domain objects *are* the mapped classes.
Elegant, removes the mapping layer, and keeps domain classes free of ORM
inheritance. Rejected narrowly: the mapping configuration becomes dense and
hard to debug, and identity-map semantics still leak into the domain. Worth
revisiting if hand-mapping becomes a genuine burden.

**A generic `Repository[T]` base class** with `get`/`list`/`save`. Rejected:
generic repositories converge on exposing a query builder, which relocates
persistence logic into `application` — the opposite of the intent.

**Offset pagination.** Rejected: `OFFSET n` scans and discards `n` rows, and
results shift under concurrent inserts, so a user paging through documents sees
duplicates and gaps. Keyset pagination on `(created_at, id)` is stable and
index-friendly.

**Raw SQL throughout.** Rejected for the command path (no compile-time safety,
easy to omit the tenant predicate). Explicitly *retained* for retrieval, where
the RRF query in ADR-0005 is clearer and faster written directly — as a
parameterised statement, never string interpolation.

## Consequences

- Mapping code exists between ORM and domain entities on the write path. It is
  concentrated in `infrastructure` and covered by tests.
- `lazy="raise"` means a missing `selectinload` fails a test rather than shipping.
  New developers will hit this and it will look like an obstacle; the ADR is the
  answer to "why is this raising?"
- Read models multiply as views multiply. Accepted: each is small, flat, and
  directly reflects a real screen.
- The worker's synchronous repository subset must stay small. If it grows toward
  parity with the async layer, that is a signal the worker is taking on
  responsibilities it should not have.
- Keyset cursors are opaque, signed strings so clients cannot craft one to
  bypass ordering or filtering.
