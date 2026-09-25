# 0004 — Authorization enforced at the repository layer

- **Status:** Accepted
- **Date:** 2026-09-09

## Context

ORBIT is multi-tenant. Section 6 of the engineering brief is unambiguous: a user
must never reach another user's documents, chunks, embeddings, conversations,
files, workspaces, or audit records.

The conventional placement for that check is the route handler or a decorator on
it. The conventional placement is also where tenant-isolation bugs come from,
for a structural reason: the check is **optional**. Every new endpoint is a fresh
opportunity to forget it, nothing fails when it is missing, and the resulting
bug is invisible in testing unless someone specifically writes a cross-tenant
test for that specific endpoint. The defect surfaces as a data breach.

Retrieval makes this sharper. A vector similarity search that omits its tenant
predicate does not error — it returns another tenant's most semantically
relevant content, ranked by how relevant it is. The failure mode is
maximally damaging and completely silent.

## Decision

Authorization is enforced **at the repository layer**, and the signature makes
it non-optional.

Every workspace-scoped repository method takes an explicit `AccessContext`
(authenticated principal plus resolved workspace membership and role) as a
required parameter, and applies the corresponding predicate inside the query.
There is no method that returns rows without one.

```python
# Not expressible: there is no overload that omits the context.
async def get(self, ctx: AccessContext, document_id: DocumentId) -> Document | None: ...
```

Consequently:

- Forgetting authorization is a **TypeError at import time under mypy strict**,
  not a runtime information leak.
- Isolation is enforced in the `WHERE` clause, so it holds for list, get,
  search, aggregate, and vector queries uniformly.
- The route layer resolves *identity*; the repository layer enforces *access*.
  These are separate concerns and the brief treats them as such.

Route handlers additionally declare the permission they require, which produces
a clear 403 with a machine-readable code rather than a bare 404. That is a
usability layer on top of the guarantee, not the guarantee itself.

## Alternatives considered

**Checks in route handlers or dependencies.** Rejected: optional by
construction, as argued above.

**PostgreSQL Row-Level Security.** The strongest available guarantee, enforced
by the database itself. Seriously considered and not rejected on merit. Deferred
because it requires a per-request `SET LOCAL` of the tenant identifier on a
pooled connection, and a leaked session variable across pooled connections is
itself a cross-tenant bug — with a failure mode that is harder to test than the
one it replaces. Revisit once connection-scoping is proven under load; it
composes with this decision rather than replacing it.

**Database-per-tenant.** Rejected: strong isolation, but migration and
connection-pool cost that scales linearly with tenant count, for a product where
cross-workspace sharing is an explicit roadmap item.

**A service-layer authorization gate above repositories.** Rejected for the same
reason as route-level checks — a layer that *may* be bypassed eventually is.

## Consequences

- Repository signatures are more verbose. That verbosity is the mechanism, not
  incidental noise, and it must not be "cleaned up".
- Genuinely system-level operations (migrations, scheduled pruning, admin
  tooling) need an explicit, auditable `SystemContext`, so privileged access is
  visible in code review rather than implicit.
- Cross-tenant denial tests are mandatory for every resource type and run in CI.
  A resource without such a test is treated as incomplete under the Definition
  of Done.
- Retrieval queries carry the workspace predicate into the pgvector query, which
  constrains index design: the ANN index must be usable alongside a selective
  equality filter. This is recorded in ADR-0005 and in
  `docs/database/indexes.md`.
