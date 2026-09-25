# Frontend (Next.js)

The ORBIT web application: sign-in, workspaces, documents with upload and live
processing status, search, grounded chat with citations, members, and settings.

Architecture: [docs/architecture/frontend.md](../docs/architecture/frontend.md)

## Running it

From the repository root:

```bash
npm run infra:up      # PostgreSQL, Redis, MinIO
npm run dev:api       # the backend (http://localhost:8000)
npm run dev:worker    # document processing
npm run dev:web       # this app (http://localhost:3000)
```

The browser only ever talks to `localhost:3000`. `/api/*` is proxied to the backend
([ADR-0009](../docs/decisions/0009-single-origin-cookie-transport.md)), which is what
lets both auth tokens be `HttpOnly` cookies.

## Commands (from the repository root)

| Command                                                   | Purpose                                                                    |
| --------------------------------------------------------- | -------------------------------------------------------------------------- |
| `npm run web:test`                                        | Unit and component tests (Vitest)                                          |
| `npm run web:typecheck` / `web:lint` / `web:format:check` | Static checks                                                              |
| `npm run web:build`                                       | Production build                                                           |
| `npm run web:api-types`                                   | Regenerate `lib/api/generated.ts` and `lib/api/access.ts` from the backend |
| `npm run verify:contract`                                 | Fail if those generated files are stale (CI runs this)                     |
| `npm run verify`                                          | Everything, backend and frontend                                           |

## Conventions worth knowing before you change something

- **Every API call goes through `lib/api`.** Components and hooks never call `fetch` or
  build a URL. Paths are the OpenAPI templates and the compiler infers the rest.
- **Query keys are declared per feature** (`features/*/api/keys.ts`), never inline.
- **Colours come from tokens.** The Tailwind palette is disabled; use `bg-surface`,
  `text-fg-muted`, `border-line`. Edit `app/globals.css` and `app/tokens.test.ts` will
  tell you if contrast broke.
- **Serif means content** (document titles, answers, excerpts); sans is the interface;
  mono is identifiers.
- **Never `dangerouslySetInnerHTML`.** Document text is untrusted; the lint rule is an
  error, not a warning.
- **Server state renders through `QueryBoundary`,** so loading, empty, error, stale and
  retry look the same everywhere.

`.claude/launch.json` (untracked) is a local convenience for the Claude desktop app's
browser preview; it is not part of the project.
