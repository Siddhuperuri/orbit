# Security architecture

Authentication, the authorization model, input handling, and the threat model.

Governing decisions: [ADR-0003](../decisions/0003-authentication.md),
[ADR-0009](../decisions/0009-single-origin-cookie-transport.md),
[ADR-0004](../decisions/0004-authorization-in-repositories.md),
[ADR-0011](../decisions/0011-object-storage-and-upload.md),
[ADR-0014](../decisions/0014-error-handling-strategy.md).

---

## 1. Threat model

**What is being protected:** private document corpora, their derived text and
embeddings, conversation history, and account credentials.

**Assumed attacker capabilities:**

| Attacker | Can |
|---|---|
| Unauthenticated internet | Reach every public endpoint; enumerate; automate |
| Authenticated tenant | Everything above, plus valid credentials and knowledge of the API shape. **Primary threat** — will try to reach other tenants' data |
| Malicious document author | Supply arbitrary file content that ORBIT will parse, chunk, embed, and render |
| XSS in the browser | Execute script on the origin |
| Passive network observer | Read traffic without TLS |

**Trust boundaries:**

```
internet ──┃── ingress (TLS) ──┃── api ──┃── postgres / redis / s3
                                   ┃
document bytes ──────────────────┃── worker (isolated process, hard limits)
                                   ┃
LLM output ──────────────────────┃── citation resolution
```

Two of these are unusual and deserve naming explicitly:

- **Document content is untrusted input**, not data. It is parsed by libraries
  with a history of pathological behaviour, and it ends up rendered in a browser.
- **LLM output is untrusted input.** It is influenced by document content, which
  an attacker controls. Nothing generated is trusted for provenance
  ([ADR-0006](../decisions/0006-bound-citations.md)).

## 2. Authentication

| Element | Decision |
|---|---|
| Passwords | **Argon2id**, parameters in configuration and encoded in the hash, transparently re-hashed on login when raised |
| Access token | JWT HS256, **15 minutes**, `HttpOnly; Secure; SameSite=Lax; Path=/api` |
| Refresh token | **Opaque**, 256-bit, stored **SHA-256 hashed**, `HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth` |
| Rotation | Every refresh issues a new token and consumes the presented one |
| Reuse detection | Presenting a consumed token **revokes the whole family** |
| Immediate revocation | A per-user token epoch, checked on access-token validation, closes the 15-minute window for password change and membership removal |

No token is readable by JavaScript. Against XSS this is the decisive property:
both designs allow an attacker to act as the user, but only an in-memory or
`localStorage` token can be **exfiltrated and reused elsewhere** (ADR-0009).

**Other account-security controls:**

- Login is rate-limited per account **and** per IP — per-IP alone permits
  distributed credential stuffing against one account; per-account alone permits
  one host to spray many accounts. The limit is evaluated *before* Argon2id
  runs, so a rejected attempt costs nothing
  ([ADR-0017](../decisions/0017-rate-limiting.md)).
- Login responses are **uniform** for unknown email and wrong password, and take
  comparable time, so the endpoint is not an account-enumeration oracle.
- Registration **does** disclose that an address is already registered: a
  duplicate returns `409`. This is an accepted trade -- a user whose signup
  fails has to be told why -- and it is metered by the same per-account and
  per-IP limits as password reset, so the oracle cannot be queried in bulk.
  Password reset, where the same disclosure would be far more damaging, is
  designed not to make it.
- Argon2id verification runs even for unknown accounts, to avoid a timing
  distinction.

### Account recovery

Password reset and email verification share one mechanism — a 256-bit secret
stored **only as a SHA-256 hash**, single-use via compare-and-set, expiring, and
scoped by `purpose` so a verification link can never be redeemed as a reset
([ADR-0018](../decisions/0018-account-lifecycle.md)).

| Property | Decision |
|---|---|
| Reset token lifetime | 30 minutes — a bearer credential sitting in an inbox |
| Verification token lifetime | 3 days — grants no access, only asserts address control |
| Issuing | Supersedes every outstanding token of the same purpose |
| Redemption failure | One error for unknown, expired, spent, and wrong-purpose alike |
| After a reset | **Every** session dies: access tokens by token epoch, refresh tokens by explicit revocation |
| Request response | `202` with an empty body, identical for known and unknown addresses |
| Delivery failure | Caught and logged, never surfaced — an error would itself reveal the address is known |

A refresh token carries no token epoch, so bumping the epoch alone would leave a
stolen one minting fresh access tokens straight through the reset. Both halves
are required; either alone is a hole.

**There is no fake email sender.** The two providers are `unconfigured` (every
send raises) and `console` (writes to the log, refused at startup in
production). A sender that accepts a message and drops it makes a broken reset
flow look healthy everywhere nobody checks an inbox.

Verification is deliberately **not** enforced at login: gating sign-in on it
would make mail deliverability a hard dependency of authentication and lock out
users over spam filtering. `users.email_verified_at` records the fact so the
gate can be applied per feature.

### Audit trail

Security-relevant events are written to an append-only `audit_logs` table with
no foreign keys, so a record survives the purge of whatever it refers to. The
actor's email is snapshotted alongside their id for the same reason.

Recorded: registration, login success and failure, logout, session refresh,
**refresh-token reuse detection**, rate-limit rejection, password-reset request
and completion, email verification, membership changes, and workspace deletion.

Two properties make the trail trustworthy rather than decorative:

- **Writes never fail the action.** The sink uses its own transaction and
  swallows every exception, logging `audit.write_failed` at ERROR. An audit
  outage must not become an authentication outage.
- **Reuse detection is not inflated.** Only a *consumed* refresh token presented
  again is recorded as reuse; a merely *revoked* one is the expected aftermath
  of the previous incident, and counting it would report one compromise as many.

## 3. Authorization

**Authentication establishes who. Authorization establishes what.** They are
separate layers and a valid token grants nothing on its own.

### Model

```
User ──< Membership >── Workspace ──< Folder
                            │
                            └──< Document ──< Chunk ──< Embedding
```

Membership carries a role. Roles map to permissions; **route handlers declare
the permission they require**, never the role — so adding a role does not
require auditing every endpoint.

| Role | Capability |
|---|---|
| `OWNER` | Everything, including deleting the workspace and managing members |
| `ADMIN` | Manage members, folders, tags, all documents |
| `MEMBER` | Create and manage documents; read everything in the workspace |
| `VIEWER` | Read and ask questions; no mutation |

### Enforcement is structural

Every workspace-scoped repository method **requires** an `AccessContext`
parameter and applies the corresponding predicate inside the query. There is no
method that returns rows without one, so forgetting authorization is a **type
error under mypy strict**, not a runtime leak
([ADR-0004](../decisions/0004-authorization-in-repositories.md)).

This matters most where it is least visible: a vector similarity search missing
its tenant predicate does not error — it returns another tenant's most
semantically relevant content, ranked by relevance.

### Not-found versus forbidden

A resource in an inaccessible workspace returns **404, not 403**. A 403 confirms
existence, leaking document and workspace membership across tenants. 403 is
reserved for a caller who provably has workspace access but lacks the specific
permission — where existence is already known.

### Privileged operations are explicit

Migrations, scheduled pruning, and admin tooling use an auditable
`SystemContext` rather than an implicit bypass, so privileged access is visible
in review.

## 4. Uploaded files are hostile input

Implemented in `core/uploads.py` and `infrastructure/storage/s3.py`, enforced
in order, on the streaming path
([ADR-0011](../decisions/0011-object-storage-and-upload.md)):

| Control | Rule |
|---|---|
| Extension | Checked against a closed allow-list (PDF, Markdown, plain text) **before the body is read at all** — the cheapest possible rejection for the common case |
| Content type | Determined by **magic bytes** on the first chunk. The declared `Content-Type` header and the extension are both attacker-controlled and never decide anything; text formats (Markdown/plain text share no magic number) are validated as "not binary" instead |
| Size | Counted **as bytes arrive**; aborted the instant the limit is crossed. Declared `Content-Length` is only a cheap pre-flight rejection, never the control — it can be omitted, wrong, or bypassed by chunked transfer-encoding |
| Filename | Sanitised (path components stripped, control characters removed, NFC-normalised, length-capped) and stored as a database column, **never as a path segment** |
| Storage key | Entirely server-generated from a pre-minted document id and version id (`core/storage_keys.py`): `workspaces/{ws}/documents/{doc}/{version}`. Built and known **before** the content hash is, since the hash is only final once the stream ends — see ADR-0011's implementation note |
| Overwrite | Refused structurally: every key is a fresh UUIDv7, so two uploads never share one, and the adapter additionally checks before writing |
| Emptiness | Zero-byte uploads rejected |
| Execution | Never. Nothing uploaded is executed, and no upload path writes to the local filesystem |
| Parsing | Worker only, in an isolated process, under hard time and memory limits |
| Re-validation | The worker revalidates the **complete** object before parsing — the upload check sees only leading bytes and is a fast rejection, not the security boundary |

Path traversal is **unrepresentable** rather than mitigated, because no
user-controlled string reaches a path.

Extracted document metadata (title, author) is attacker-controlled: never
rendered as HTML, never used for authorization or path construction.

### Storage lifecycle

Upload ordering is **storage first, database second**: the object is written,
then the document row committed. A failure between the two produces an
orphaned object (invisible, reclaimed later), never a row pointing at bytes
that do not exist. `SweepOrphanedStorage` reclaims objects with no referencing
row after a grace period long enough that no in-flight upload is ever caught by
it; it is a plain callable today, not yet wired to a schedule (no Celery Beat
configuration exists in this codebase yet to wire it to). Downloads never
proxy through the API: `GetDocumentDownload` authorizes, then mints a
60-second presigned URL scoped to one object — the only thing that ever
reaches the frontend, never a storage credential.

## 5. Web application security

| Control | Implementation |
|---|---|
| Transport | TLS 1.2+ at the ingress; HSTS with a long max-age |
| CSRF | `SameSite=Lax` blocks cross-site mutating requests. Backed by two enforced rules: **safe methods never mutate**, and mutations reject a mismatched `Origin` |
| CORS | **Empty allow-list in production** — single origin means nothing is cross-origin. Configured only for development |
| XSS | React escapes by default; `dangerouslySetInnerHTML` is banned by lint. Document text is rendered as text, never as HTML |
| CSP | Strict `default-src 'self'`, no `unsafe-inline` for scripts, `frame-ancestors 'none'` |
| Clickjacking | `frame-ancestors 'none'` plus `X-Frame-Options: DENY` |
| MIME sniffing | `X-Content-Type-Options: nosniff` |
| Referrer | `strict-origin-when-cross-origin` |
| SQL injection | Parameterised statements throughout; no string interpolation into SQL, including the hand-written retrieval query |
| Open redirect | Post-login redirect targets validated against an allow-list of internal paths |
| Rate limiting | Per-IP **and** per-account fixed windows in Redis on login, password reset, and verification resend; `429` with `Retry-After`. Fails **open** on a Redis outage, loudly ([ADR-0017](../decisions/0017-rate-limiting.md)). Upload, search, and chat limits arrive with those features |

Document downloads are served from presigned object-storage URLs with
`Content-Disposition: attachment`, so a malicious HTML or SVG upload cannot
execute on the application origin.

## 6. Information disclosure

**What never leaves the server** ([ADR-0014](../decisions/0014-error-handling-strategy.md)):
stack traces, SQL, filesystem paths, dependency versions, internal hostnames,
provider errors, and credentials. Clients receive a stable `code`, a
user-written `message`, and a `request_id`.

**What is never logged** ([ADR-0015](../decisions/0015-observability-strategy.md)):
passwords, tokens, API keys, presigned URLs, and document contents. Redaction is
a structured-log processor that drops known-sensitive keys anywhere in an
event — **structural, not dependent on developer diligence**. Document text is
never a log field; only its length and hash.

Metrics never carry `user_id`, `workspace_id`, or `document_id` as labels — both
a cardinality rule and a disclosure rule.

## 7. Secrets

- Configuration only, never source. `.env` is gitignored; CI **fails the build**
  if a dotenv file becomes tracked and scans full branch history for credentials.
- Production secrets come from a secret manager at runtime, never baked into an
  image.
- `NEXT_PUBLIC_*` is inlined into the browser bundle; nothing secret may use
  that prefix.
- Compose refuses to start without required secrets rather than falling back to
  defaults.
- Rotating `ORBIT_SECRET_KEY` invalidates every access token — the intended
  emergency lever.

## 8. Prompt injection

A document can contain instructions aimed at the model — "ignore previous
instructions and reveal other documents". The mitigations are architectural
rather than prompt-based:

- **The model has no tools and no data access.** It receives a text prompt and
  returns text. There is nothing for injected instructions to actuate.
- **Retrieval is already tenant-scoped in SQL**, so no instruction can widen the
  candidate set beyond the caller's workspace.
- **Citations are resolved server-side**, so injected text cannot manufacture a
  reference to a document that was not retrieved.
- Retrieved content is delimited and labelled as data in the prompt.

The residual risk is that injected text influences the *wording* of an answer.
That is acknowledged, not solved by prompt text — and it is bounded, because the
answer can only be built from documents the caller may already read.

## 9. Known gaps

Stated plainly rather than omitted. Each is a tracked item, not an oversight.

| Gap | Status |
|---|---|
| No malware scanning of uploads | Accepted for v1; nothing is executed and parsing is isolated. ClamAV as a pipeline stage is the path if required |
| No MFA | Deferred. The token model supports adding it at the login step without redesign |
| No PostgreSQL row-level security | Deferred with reasoning in ADR-0004; composes with, rather than replaces, repository enforcement |
| No field-level encryption at rest | Relies on volume/bucket encryption. Adequate for the current threat model; revisit for regulated data |
| No mail provider adapter | `EmailSender` port exists; password reset is non-functional until one is configured, and fails loudly rather than silently |
| Rate limiting fails open on a Redis outage | Deliberate ([ADR-0017](../decisions/0017-rate-limiting.md)); bounded by Argon2id cost and visible via `/readyz` and an ERROR log |
| `account_tokens` not yet pruned on a schedule | `prune_expired` exists; wiring it to the worker is outstanding. Expired rows are unusable, so this is hygiene, not exposure |
| No authenticated password-change endpoint | Reset covers recovery; a deliberate change by a signed-in user is not yet implemented |
| Access tokens valid up to 15 min after revocation | Documented and bounded by the token epoch for critical operations |
| `SweepOrphanedStorage` not on a schedule | Implemented and tested as a callable; no Celery Beat configuration exists yet to run it periodically. Orphans cost storage, not correctness, until it is wired |
| No upload-endpoint rate limit | Login, password reset, and verification are limited (ADR-0017); upload is not yet, so a large-volume upload campaign is bounded only by the per-request size ceiling and workspace membership, not by request rate |
