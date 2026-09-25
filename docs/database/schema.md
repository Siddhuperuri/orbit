# Schema

15 tables, 29 indexes, 40 check constraints, 25 foreign keys.

Defined in [`backend/src/orbit/infrastructure/db/models/`](../../backend/src/orbit/infrastructure/db/models/)
and created by [`0001_initial_schema`](../../backend/alembic/versions/0001_initial_schema.py).
Index rationale is in [indexes.md](indexes.md).

---

## Shape

```
users ──< workspace_members >── workspaces
  │                                 │
  │                                 ├──< folders (self-referencing tree)
  │                                 ├──< tags ──< document_tags >── documents
  │                                 │                                  │
  │                                 │                                  ▼
  │                                 │                        document_versions
  │                                 │                          │           │
  │                                 │                          ▼           ▼
  │                                 │                       chunks   processing_jobs
  │                                 │                          │
  └──< conversations ──< messages ──< message_citations ┄┄┄┄┄┄┄┘
       (owned by user)                                  (nullable link)

refresh_tokens ── users            audit_logs (no foreign keys, by design)
```

## Entities that were evaluated and *not* built

The brief listed these; analysis rejected two of them. Both absences are
decisions.

| Proposed | Verdict | Reason |
|---|---|---|
| `permissions` | **Not built** | The role→permission mapping is a closed, static set that varies by neither tenant nor deployment. A table would add a database round trip to answer what a frozen mapping answers, and create a second place for the rule to live — and therefore a way for the database and the code to disagree. It lives in [`domain/access.py`](../../backend/src/orbit/domain/access.py). If per-workspace custom roles ever become a requirement, that is when a table earns its place, and this mapping becomes its seed data. |
| `embeddings` | **Not built** — folded into `chunks` | One chunk has exactly one embedding, so a separate table adds a join to the single hottest query in the system to model a relationship that is not one-to-many. Each row records its embedding space, input hash, and `embedded_at`; a same-width model change is an in-place re-index and a width change an expand/contract column migration ([embeddings.md](embeddings.md), ADR-0020). |
| `document_versions` | **Built, and load-bearing** | See below — it is the decision the rest of the content model hangs from. |

## The decision that shapes the content model

**Identity is separated from content.**

A `documents` row is a document's *identity*: its title, its folder, its tags,
and the conversations that cite it. A `document_versions` row is its *content*:
the stored bytes, their hash, and the processing outcome for those bytes.

Re-uploading a revised file therefore preserves everything a user has organised
around the document while replacing what it says. The alternative — putting
`storage_key` and `status` directly on `documents` — makes "replace this file"
either destroy history or require a parallel history table that duplicates
identity columns.

**Only the current version has chunks.** Chunks are derived, rebuildable, and
exist to be retrieved. Keeping a superseded version's chunks would pollute
retrieval with text the document no longer contains and multiply the ANN index
for no benefit. Superseded versions keep their stored object — so a restore is
possible — but their chunks are hard-deleted.

**"Current" is a flag, not a pointer.** `document_versions.is_current` with a
partial unique index on `(document_id) WHERE is_current` gives exactly one
current version per document. A `documents.current_version_id` pointer would be
a second source of truth that can disagree with the flag.

## Deletion strategy

| Kind | Strategy | Why |
|---|---|---|
| `users`, `workspaces`, `documents`, `folders`, `conversations` | **Soft delete** (`deleted_at`) | An accidental delete of someone's corpus must be recoverable. |
| `document_versions`, `chunks` | **Hard cascade** | Derived and rebuildable; worthless without their parent. |
| `workspace_members` | **Hard delete** | Revoking access must actually revoke it. A soft-deleted membership row is one forgotten `WHERE deleted_at IS NULL` away from privilege escalation. |
| `messages`, `message_citations` | **Hard cascade** from conversation | A conversation is one artefact. |
| `audit_logs` | **Never deleted** by application code | It is the record of what happened, including deletions. |

Every query filters soft-deleted rows by default. Including them is explicit and
restricted to administrative paths.

### What survives what

| Event | Result |
|---|---|
| User soft-deleted | Account inaccessible, `token_epoch` bumped so live sessions die immediately. Their documents stay — they belong to the workspace. Their private conversations stay until purge. |
| User hard-purged | `documents.created_by_user_id` → `NULL` (provenance lost, content kept). Memberships and conversations cascade away. Audit records keep the snapshotted `actor_email`. |
| Workspace soft-deleted | Invisible; contents untouched and recoverable. |
| Workspace purged | Cascades to members, folders, tags, documents, versions, chunks, jobs, conversations. **Audit records survive** — they have no foreign key. |
| Document soft-deleted | Invisible immediately. Chunks, embeddings, and the stored object are reclaimed asynchronously and idempotently. |
| Version superseded | Chunks deleted; stored object retained so the version can be restored. |

### Erasure requests

A genuine "delete my data" request is **anonymisation plus soft delete**, not a
hard delete: `email` and `full_name` are replaced with tombstone values, the
password hash is cleared, and `deleted_at` is set. A hard delete would erase the
actor from audit history and orphan workspace content.

## Ownership rules

Workspaces have **no `owner_user_id` column**. Ownership is a membership role, so
one query answers "who owns this" and there is no second place for the answer to
drift. `created_by_user_id` is retained as immutable provenance — a historical
fact, not a live authority.

**A workspace always has at least one owner.** No table constraint can express
"at least one row with this role", so it is enforced in
[`SqlMembershipRepository._guard_last_owner`](../../backend/src/orbit/infrastructure/db/repositories/memberships.py)
and covered by tests. `SELECT … FOR UPDATE` over the owner rows serialises two
concurrent demotions; without it both would see two owners, both would proceed,
and the workspace would end up with none — unadministrable, recoverable only by
direct database access.

## Workspace boundaries

Three mechanisms, in increasing order of strength.

**1. Every tenant-scoped table carries `workspace_id`**, so isolation is one
predicate rather than a join that a query can forget.

**2. Repository signatures require an `AccessContext`.** There is no method that
returns rows without one, so omitting a tenant filter is a `mypy --strict` error,
not a silent cross-tenant read (ADR-0004).

**3. Composite foreign keys make some cross-tenant references *unrepresentable*.**
This is the strongest of the three, because no application bug can bypass it:

```sql
FOREIGN KEY (workspace_id, folder_id)
    REFERENCES folders (workspace_id, id)
```

Filing a document into another tenant's folder, tagging it with another tenant's
tag, or attaching a chunk to another tenant's version are all rejected by the
database. Eight such constraints exist; each requires a `UNIQUE (workspace_id,
id)` on the referenced table, which is why those apparently redundant
constraints are present.

## Concurrency

| Mechanism | Where | Prevents |
|---|---|---|
| Optimistic `version` column | `documents`, `workspaces`, `folders`, `conversations` | Lost updates. Two simultaneous renames produce a visible `409` for the loser instead of silently discarding the first write. |
| `SELECT … FOR UPDATE` | Adding a version; last-owner guard | Two uploads computing the same next version number; two demotions each believing another owner remains. |
| Partial unique index | `(document_id) WHERE is_current` | Two current versions. The demote and the insert are ordered inside one transaction, so no reader observes zero or two. |
| `SELECT … FOR UPDATE` + partial unique index | Starting a conversation turn; `messages (conversation_id) WHERE status = 'pending'` | Two questions claiming the same ordinals, or two answers generating into one thread at once (ADR-0022). |
| Database-generated timestamps | Everywhere | A clock-skewed application host writing timestamps that disagree with the database's own ordering. |

Append-only tables (`chunks`, `audit_logs`, `messages`) deliberately have **no**
`version` column: nothing updates them, so lost-update protection would be dead
weight on the highest-volume tables in the schema. An assistant message's single
`pending` → terminal transition (migration 0005) is a conditional `UPDATE …
WHERE status = 'pending'`, not an edit: it happens once, before the message is
ever shown as an answer.

## Constraints that encode decisions

Most check constraints are ordinary sanity rules. These four encode
architectural decisions, and relaxing one would silently undo the decision:

| Constraint | Enforces |
|---|---|
| `ck_document_versions_ready_versions_have_chunks` | A `READY` version with zero chunks answers no questions while looking healthy in a list. ADR-0012 makes that case `FAILED` with a distinct reason; this is what makes the alternative unrepresentable. |
| `ck_document_versions_failed_versions_have_a_code` | A failure must say why. Ambiguous states are unrepresentable. |
| `ck_document_versions_terminal_versions_have_processed_at` | A version cannot claim to be finished without recording when. |
| `ck_folders_root_folders_have_depth_zero` | Keeps `depth` honest, so the bound on recursive subtree queries actually holds. |

## Duplicate uploads

Deduplication is a **partial unique index over current content**:

```sql
CREATE UNIQUE INDEX uq_document_versions_workspace_content_current
    ON document_versions (workspace_id, content_sha256) WHERE is_current;
```

Three properties follow, each deliberate:

- **Workspace-scoped, not global.** A global namespace would leak the existence
  of another tenant's document through a deduplication hit.
- **Current versions only.** A constraint across all historical versions would
  reject the legitimate case of reverting a document to earlier content, because
  the superseded row still holds that hash.
- **Enforced, not checked.** A check-then-insert is a race between two concurrent
  uploads of the same file.

## Failed processing and orphans

The state machine is `PENDING → PROCESSING → READY | FAILED`, four states, all
explicit. **`FAILED` means the document is the problem; `PENDING` means we are** —
a provider outage returns a version to `PENDING` rather than telling the user
their file is broken.

`document_processing_jobs` records one row **per attempt** rather than a mutable
counter, because the question worth answering during an incident is "what
happened on each try": which error, how long, under which request id.

Since migration `0003`, the job row is the source of truth for pending work
([ADR-0019](../decisions/0019-asynchronous-processing-pipeline.md)):
`scheduled_for` makes backoff durable, `worker_id` + `lease_expires_at` form the
lease every worker write is fenced on, `stage` records how far an attempt got,
`run_attempt` bounds retries within one processing run, and `failure_kind`
(`transient` | `permanent` | `defect`) decides whether a failure is retried.
Check constraints make a running job without a lease, or a failed job without a
classification, unrepresentable; `uq_jobs_active_per_version` allows at most one
queued-or-running job per version. `chunks.content_sha256` records each chunk's
hash.

| Orphan | Detection | Reclamation |
|---|---|---|
| Chunks whose version is gone | Impossible — `ON DELETE CASCADE` | n/a |
| Stored objects with no row | Sweep from storage, checking `ix_document_versions_storage_key` | Delete after a grace period longer than the longest plausible in-flight upload |
| Jobs whose message was lost | `ix_jobs_queued_scheduled_for` + `enqueued_at` older than the grace period | Re-published by the recovery sweep. Redis is disposable, so the queue is not treated as reliable |
| Jobs whose worker died | `ix_jobs_running_lease_expires_at` | Attempt recorded `WORKER_LOST`; next attempt scheduled by the recovery sweep |
| Expired refresh tokens | `ix_refresh_tokens_user_id_expires_at` | Scheduled prune |

## Audit

`audit_logs` is the **only table with no foreign keys**, and that is deliberate.
An audit record is a historical assertion. A foreign key would force one of two
wrong behaviours when the referenced row is purged: block the deletion (so a
workspace can never be fully removed) or destroy the audit trail alongside it (so
the record of a deletion disappears when the deletion completes). Both defeat
the purpose.

Identifiers are therefore plain indexed values, and `actor_email` is snapshotted
at write time so a record still names someone after the account is gone.

Immutability is enforced by **permission, not constraint**: the application role
is granted `INSERT` and `SELECT` on this table and nothing else. That grant is a
provisioning step, documented in `docs/operations/provisioning.md`.

Partitioning by month is the obvious scaling move and is deliberately not done
yet — it is a change to one table, and doing it before there is volume to
measure would be guesswork.

## Extensions

`vector`, `pg_trgm`, and `uuid-ossp` are **not** created by the migration.
`CREATE EXTENSION` requires privileges the application role must not hold, so
they are provisioned out of band — by `docker/postgres/initdb/` locally and by
the database platform in production. The migration fails loudly without
`vector`, which is correct: the schema is unimplementable without it.

## Primary keys

UUIDv7 throughout, generated in Python
([`core/ids.py`](../../backend/src/orbit/core/ids.py)).

A UUIDv4 key is uniformly random, so every insert lands on a random leaf of the
B-tree, dirtying a new page each time and fragmenting the index. A v7 key carries
a millisecond timestamp in its leading 48 bits, so inserts append to the
right-hand edge — a sequence's access pattern without a sequence's coordination
or its enumerable values. It also makes `id` a meaningful chronological
tiebreaker, which is what lets keyset pagination use it.

Generation is application-side because the identifier is needed *before* the
`INSERT`, so a caller can build a whole object graph in memory and write it in
one round trip.

## Migrations

Alembic, **expand/contract only**. Every migration must be backward-compatible
with the release before it, because both versions run against the same schema
during a rolling deploy. Never rename or drop a column in the same release that
stops using it.

The integration suite runs **upgrade → downgrade → upgrade** against a real
database on every run. A downgrade that leaves an artefact behind — an enum type
is the usual culprit, since `DROP TABLE` does not remove one — breaks the second
upgrade, and that is exactly the path a production rollback-then-roll-forward
takes.
