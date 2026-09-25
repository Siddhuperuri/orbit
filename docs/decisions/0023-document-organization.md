# 0023 — Document organization: archive, folders, tags, honest progress, and the text viewer

- **Status:** Accepted
- **Date:** 2026-09-24

## Context

M3 stored documents, versions and (as schema only) folders and tags. The document
experience needs them to *behave*: filing, tagging, archiving, browsing versions, reading
what was indexed, and watching processing — under concurrent editing by several members,
with roles that see different controls, and without ever showing progress the backend
cannot vouch for.

Each of those hides a decision that is easy to get subtly wrong.

## Decision

### Archive is a visibility flag, not a state machine

`documents.archived_at` (nullable timestamp). An archived document is hidden from the
default list and **excluded from retrieval** (`search._visible` filters it), but nothing is
deleted: chunks and files are kept, so restoring is one idempotent write and needs no
re-indexing. Archiving and restoring are idempotent and, like delete, are `document:update`
/ `document:delete` respectively.

### Folders form a tree; the parent row is the lock

Folders are deletable **only when empty** (no documents, archived ones included, and no
subfolders). ORBIT never moves or removes content on a user's behalf. The check races with
filing, so it is serialised on the folder row: filing a document takes `FOR SHARE` on the
target folder, deleting takes `FOR UPDATE`. This is exercised with two real connections and
verified by mutation (removing either lock fails the test). Depth (16) and counts (500
folders, 200 tags per workspace, 20 tags per document) are bounded in
`domain/organization.py`.

Not built: re-parenting a folder. It needs cycle detection under concurrency and was
judged not worth the risk in this milestone.

### Tags are idempotent, coloured from a closed palette, and versioned

Attach/detach are `PUT`/`DELETE` on `…/tags/{tag_id}` and idempotent. Colours are five theme
tones (`neutral, accent, success, warning, danger`), never free-form, so every tag is legible
in both themes. Tags carry `version` for optimistic concurrency on rename/recolour.

### Listing: server-side filters, sort-bound keyset cursors

Search (title substring, escaped `LIKE`, served by a `pg_trgm` GIN index — an extension
provisioning already creates), status, folder / unfiled, tags (all-of), archive view (`active` or `archived`; deliberately no "both") and five
sorts are all query parameters. A cursor is HMAC-signed *and bound to its sort*, so a cursor
from one ordering cannot be replayed against another. Nothing is filtered over "what happens
to be loaded", because that is silently wrong the moment there is a second page.

### Optimistic concurrency everywhere a user edits

Edits send the version the page last read (`expected_version`); a stale write is a `409`. The
UI keeps what the user typed, refetches, and makes the next Save an explicit overwrite of the
now-current version. A document that is deleted or made inaccessible elsewhere is discovered
on the next refresh and shown as **gone**: the last copy stays readable, labelled, and every
control that would only 404 is removed (`useDocumentAccess`).

### Progress is a step, never a percentage

The API exposes a coarse `status` and, only while pending/processing, the `processing_stage`
the worker last reported (derived from the latest job row). "Indexing" is the `embed`/`index`
stages. Nothing finer exists, so nothing finer is drawn: the stepper says *which* step, and
after a failure it does not claim to know which steps were reached. Client-side upload shows
`sending` with a real byte fraction when the browser reports one (else "unknown"), then
`saving` once every byte is sent; it never shows 100% before the server answers.

### The viewer shows the indexed text, not the file

The viewer renders the passages ORBIT actually indexed, in order, with chunk overlap removed
exactly (by character offsets) so nothing is repeated. This is deliberate: it is what search
and answers cite, so a citation can land on the passage it quoted (`?passage=N&v=V`), and a
link made against a since-replaced version says so. It is not a rendering of the original
(layout, images, fonts); the original is one click away by download. Superseded versions keep
their file but lose their text.

### Authorization in the UI is subtraction

A control the caller cannot use is **absent, not disabled**. The role→permission map is
generated from the API, and the server enforces independently (a viewer's 13 hidden mutations
were each verified to return `403`).

## Alternatives considered

- **Archive as a status enum or a separate table.** Rejected: a nullable timestamp answers
  "is it archived, and since when" with no state transitions to keep consistent.
- **Cascade-delete or auto-move on folder delete.** Rejected: silently destroying or moving
  content is the failure users remember. Refusing with the counts and a link is cheap.
- **Offset pagination.** Rejected: rows shift under concurrent inserts and deletes; keyset
  cursors do not skip or repeat.
- **Client-side filtering of the loaded page.** Rejected: wrong beyond page one.
- **A progress bar from stage position.** Rejected: it would be a guess dressed as a
  measurement. Stages are unevenly sized; the bar would stall and jump.
- **Rendering the original file (PDF.js etc.) in the viewer.** Deferred: it would show text
  that may differ from what was indexed, undermining citations. Revisit with page-anchored
  citations.
- **Disabled controls with tooltips for roles that cannot act.** Rejected: they advertise
  actions the user can never take.

## Consequences

- Migration `0006` needs `pg_trgm`. It cannot create it (the application role must not hold
  that privilege); provisioning does (`docker/postgres/initdb`), and the migration fails at
  the statement that needs it if it is missing, rather than later as a slow query.
- Deleting a folder needs a two-step for the user (empty it first). That is intentional.
- The uploader is shown as "you" / a short member id: the API has no user directory.
- Unbounded folder depth is capped, so a pathological tree is refused rather than rendered.
- No E2E suite yet; the flows are covered by component tests against the real modules and by
  a manual pass against the running stack (see the milestone report).
