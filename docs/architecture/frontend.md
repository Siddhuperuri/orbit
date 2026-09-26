# Frontend architecture

Next.js App Router, React 19, TypeScript (strict), Tailwind v4, shadcn/ui-pattern
primitives on Radix, TanStack Query, React Hook Form, Zod.

Data-fetching rationale is in
[ADR-0016](../decisions/0016-frontend-data-fetching.md); the single-origin and
cookie model is in [ADR-0009](../decisions/0009-single-origin-cookie-transport.md).
Both carry M7 amendments recording where the build found the original text to be
wrong or incomplete.

---

## 1. Organisation

Feature-oriented. `app/` is routing and composition only; anything with logic lives
in a feature.

```
web/
  app/
    auth/                       login  register  forgot-password  reset-password  verify-email
    (app)/                      the authenticated shell (AuthGate + AppShell)
      account/   workspaces/new/
      workspaces/[workspaceId]/
        documents/  documents/[documentId]/  search/  chat/  chat/[conversationId]/
        settings/   settings/members/
    layout.tsx  providers.tsx  session-events.tsx  error.tsx  global-error.tsx  not-found.tsx
  features/
    auth/  workspaces/  documents/  search/  chat/  settings/  system/
      api/          endpoints, query keys, query/mutation hooks
      components/   feature UI
      hooks/  lib/  schemas/  state/  streaming/ | upload/
  components/
    ui/           primitives: button, input, dialog, sheet, dropdown-menu, command...
    feedback/     error/empty/stale states, QueryBoundary, toasts, ConfirmDialog
    forms/        form-level error, password input
    layout/       app shell, sidebar, top bar, page header, command palette
  lib/
    api/          client, session, sse, upload, errors, describe-error, types,
                  generated.ts (OpenAPI), access.ts (role permissions)
    query/  theme/  a11y/  hooks/  utils/  forms/  navigation.ts
  scripts/generate-api-types.mjs
```

Auth pages live under a real `/auth/` segment because the backend's email links
point there (`ORBIT_PASSWORD_RESET_URL_TEMPLATE`, `ORBIT_EMAIL_VERIFICATION_URL_TEMPLATE`).

There is **no `folders` feature**. `folder_id` exists on documents and as a list
filter, but the API has no folder or tag endpoints yet; a feature with nothing to
call would be a shell pretending to work. `features/system` holds the build-identity
query shown under *Account → About*.

A component used by one feature belongs to that feature. Premature sharing is how a
design system becomes a junk drawer.

## 2. Server and client boundary

**Server Components render chrome. TanStack Query owns all application data.**
Nothing private is in the page HTML: every document, result, and message is fetched
by the browser, and the API authorises each request.

> If the data can change while the user is looking at it, or the user can change
> it, it belongs to TanStack Query.

### The authentication gate is client-side, and that is forced

ADR-0016 said Server Components would handle the auth gate. They cannot. Both
cookies are scoped to `/api` (`Path=/api`, `Path=/api/v1/auth`), so the browser
attaches neither to a *page* request, and a server render cannot tell whether anyone
is signed in. `AuthGate` therefore resolves `/auth/me` in the browser, shows a
shell-shaped loading state, and redirects to `/auth/login?next=…` on a 401. It exists
for experience, not protection: the API is what refuses unauthorised requests.

There is no BFF layer, and Route Handlers do not proxy authenticated requests.

## 3. The API layer

`lib/api` is the only place that speaks HTTP; components and hooks never call `fetch`.

- **Typed from the contract.** `api.get("/api/v1/workspaces/{workspace_id}/documents", …)`
  infers path parameters, query, JSON body, and response from the generated OpenAPI
  types. A renamed or removed route stops compiling; a missing path parameter is a
  compile error.
- **Types are generated, not written.** `npm run web:api-types` exports the schema by
  importing the backend (no server, no database) and writes `lib/api/generated.ts`.
  It also exports the role → permission map (`orbit.domain.access`) to
  `lib/api/access.ts`, which the UI uses only to avoid *offering* actions a role
  cannot perform. CI's `contract` job fails when either file is stale.
- **Zod where types cannot reach.** Form input, the error envelope, and — because
  OpenAPI cannot describe Server-Sent Events — the three SSE event payloads. A stream
  frame that fails validation is surfaced as a failed answer, not trusted.
- **Errors.** One module parses the envelope (`code`, never message text).
  `describeError` turns any failure into user-facing copy; toasts, inline states, and
  form errors all render from it. Internal errors never show server text — only the
  request reference.
- **Uploads** are a raw-body `POST` (the backend deliberately avoids multipart), sent
  with `XMLHttpRequest` because `fetch` cannot report upload progress.

### Session handling

The access token lives 15 minutes and refresh tokens rotate with **reuse detection**
(ADR-0003): presenting an already-consumed refresh token revokes the whole session. So
two requests that both notice an expired token and both refresh would sign the user
out. `lib/api/session.ts` guarantees at most one refresh in flight:

- within a tab, concurrent 401s share one promise;
- across tabs, a Web Lock serialises them, and a `localStorage` timestamp lets the
  second tab see that a refresh already happened after its own request was sent, and
  simply replay;
- a transient refresh failure (5xx, network) does not sign the user out;
- sign-out in one tab is broadcast to the others.

`?next=` is followed only if it resolves to a same-origin path
(`safeNextPath`) — including after normalisation, since `/.//evil.example` resolves
to `//evil.example`.

## 4. Long-running work outlives pages

Two things take long enough that navigating away must not cancel them, so each lives
in a provider at the workspace layout rather than in a component:

- **Uploads** — a queue with bounded concurrency, progress, cancel, and retry;
  leaving the tab mid-upload asks for confirmation.
- **Answers** — one stream per conversation. Starting one is an ordinary function call
  from an event handler, not an effect (an effect that starts network work is killed by
  React's development double-mount).

Both are plain TypeScript classes bridged with `useSyncExternalStore`, and unit-tested
without rendering anything.

### Polling is scoped and self-terminating

Only documents in a non-terminal state are polled, each on its own backoff
(1.5 s → 15 s), and the poll for a document stops the moment it reaches `ready` or
`failed`. With nothing in progress there are no queries at all. There is no interval
on the list.

## 5. Chat

Streaming uses `fetch` and a `ReadableStream` reader (`EventSource` cannot `POST`).
Events are `started`, `retrieval`, `delta`…, then one `done` or `error`.

- `delta` text is provisional and shown as plain text; the `done` message replaces it
  with formatted text and resolved citations. Citations are never attached mid-stream.
- **Grounding is visible.** Anything other than `grounded` gets a labelled notice, a
  dashed rule, and muted text — an uncited answer must not look like a cited one
  (ADR-0006).
- Inline `[S1]` markers render as buttons that open the matching source, which shows the
  verbatim stored passage. Only handles that resolved to a real citation are linked.
- Answers render through `react-markdown` (never raw HTML), with headings shifted below
  the page's own outline.
- A question is user content and stays out of URLs. The palette hands one to the
  composer in memory; only a document scope (an id) appears in an address.

## 6. Design system

The brief is explicit that ORBIT must not look AI-generated. The direction is **a field
notebook for machines**: warm paper and charcoal ink, a monospaced voice for labels and
status, dark "keycap" controls with one orange signal, and ASCII -- text drawn as an
image -- for the few places the product shows off. It merges two references: the
hairline column grid and pointer-lit rules of denmu.com, and the paper/charcoal split,
pill navbar, keycaps and ASCII canvases of contentarchitecture.dev. The dark theme is the
same notebook under a desk lamp. Expressed as constraints:

- **Two typographic voices** (Geist, self-hosted from npm; nothing loads from a third
  party). Sans for statements and the interface: page titles are bold, tight and large
  (`display-title`, sized in container units so they fill their column), and answers and
  body text are sans too. Mono for labels and identifiers: the small uppercase tags that
  name a place, a status, a shortcut (`label-micro`, `label-caps`).
- **Colour has three jobs.** Ink (paper and charcoal) is the interface; the primary
  action is a dark key and the current place an inverted block. **Signal orange** is the
  dot on a key, focus, links and citations. The **highlighter** marks evidence: a matched
  term, a cited passage. Semantic colours always carry an icon and a label; tags and
  statuses are outlined, never filled.
- **The navigation floats.** A dithered pill (`.dither`) over the middle of the page holds
  the sections as mono labels, the current one lit, with a ticker beneath it carrying the
  workspace name and your role (`components/layout/floating-nav.tsx`); the workspace
  switcher, search and account are pills at the corners. Below `lg` the pill shrinks to the
  current section and a menu button that opens the full navigation as a drawer.
- **ASCII is a first-class medium.** `AsciiVortex` sets a phrase along a spiral that turns
  slowly and bends toward the pointer (sign-in, 404); `AsciiField` is a sparse field of
  drifting characters that flare where the pointer passes (chat). Both are canvas
  (DPR-aware, paused off-screen, one still frame under `prefers-reduced-motion`),
  `aria-hidden`, and read their colours from the theme's variables at draw time.
- **The grid stays.** Hairline column rules (2 / 4 / 6 by breakpoint) run behind every
  page, and light up in the accent near the pointer; page bodies sit on chosen columns.
- **Softly rounded.** Radii run 4-20 px: keys 8, cards 16-20, pills full. Hairlines do most
  of the separating; a little shadow marks what floats or presses.
- **Motion is CSS-first.** The rules draw in, titles rise word by word (`KineticText`),
  the rest of a header follows in a short stagger (`.enter`); rows and sections rise on
  scroll (`.scroll-reveal`, a `view()` timeline, progressive); fills sweep across rows and
  ink rises through outlined buttons on hover; the ticker slides. Everything is off under
  `prefers-reduced-motion`, and pointer effects are off on touch. Pointer positions are
  written through the CSSOM once per frame -- no React state, no inline `style` attribute
  for a Content-Security-Policy to refuse.
- **Tokens, not values.** The Tailwind palette is switched off (`--color-*: initial`), so
  `bg-red-500` does not exist. A subtree can take the other theme's tokens with its own
  `data-theme` (the sign-in art panel is always charcoal).
- **Contrast is a test.** `app/tokens.test.ts` parses `globals.css` and asserts WCAG AA for
  every text pair and 3:1 for focus rings and control borders, in both themes.
- **Focus** is one global `:focus-visible` outline. Text fields are the one exception: an
  accent border and a focus-coloured halo (the search field draws an accent line across its
  rule), which reads as "typing here".
- Theme is `system`/`light`/`dark`, applied before first paint by `public/theme-init.js`
  (a same-origin file, so no inline script and no CSP `unsafe-inline`).
- Document titles default to a readable form of the filename (`title_from_filename` in the
  backend domain), and a list row shows the file's *kind* and page count beneath the title.

## 7. Every state is designed

`QueryBoundary` is the single rendering policy for server state:

| State | Presentation |
|---|---|
| Loading | Skeletons shaped like the final layout; one "Loading" announced per region |
| Empty | Says what belongs here and offers the action that fills it |
| Error, nothing to show | `role="alert"`, cause, request reference (copyable), **Try again** only when a retry can help |
| Refresh failed, data shown | The data, kept, under a **stale** notice with Retry |
| Offline | The data, labelled stale |
| Partial | A processing document renders with its status; a failed one shows the backend's reason |
| Destructive | Names the resource; irreversible actions require typing its name; initial focus is Cancel |

## 8. Accessibility

Practical WCAG 2.1 AA, treated as a correctness requirement.

- Semantic landmarks; one `<h1>` per page; a skip link; focus moves to `<main>` after
  client-side navigation (and is *not* stolen on first load).
- Dialogs and drawers are Radix: focus trapped, restored on close, `Escape` closes.
  Focus that would fall to `<body>` (a dialog opened from a menu item whose menu has
  unmounted) is caught and returned to the owning control.
- Form errors are linked by `aria-describedby`, marked `aria-invalid`, and focus goes to
  the first invalid field. Status changes (processing finished, upload done, answer
  ready) go through a live region.
- `Ctrl/⌘+K` opens a command palette (a combobox). Only modified shortcuts are used, so
  no character-key shortcut needs a way to be switched off (WCAG 2.1.4).
- Touch targets grow to 44 px on coarse pointers; the layout is verified at phone,
  tablet, and desktop widths.

Verified with axe-core **in a real browser** (colour contrast cannot be evaluated in
jsdom) on every screen in both themes, plus keyboard passes. Automated checks find
perhaps half of real issues; they are not treated as sufficient.

## 9. Testing

- `npm run web:test` — Vitest. Pure logic (API client, SSE framing, single-flight
  refresh, stream state machine, upload queue, redirect safety, formatting, polling),
  the design tokens, and the shared state components.
- The API client tests stub `fetch` at the network boundary; behaviour against the real
  backend is checked separately, end to end.
- **Component tests** render real components inside real providers (`test/render.tsx`:
  query client, user, workspace + role, upload queue, answer streams) and spy on the
  `*Api` objects, so an unexpected call is visible. `test/navigation.ts` keeps a real URL
  behind `next/navigation`, so URL-driven state round-trips. They cover role-based
  controls (a viewer's page has *no* write controls), delete/archive confirmations,
  rename conflicts, the "document is gone" state, list loading/empty/error/paging/URL
  state, versions and uploads, and folder deletion rules.

## 10. Known gaps

Stated so they are not mistaken for done.

- **No end-to-end suite.** M7's plan calls for Playwright with automated axe; the
  screens were verified by hand against the running stack (see the M7 report), not by a
  repeatable suite.
- **Lists are not virtualised.** They are server-paginated with "Load more"; a very long
  loaded list is not windowed.
- **No bundle-size budget** is enforced in CI yet.
- **The viewer shows the indexed text, not the original file** (deliberately; ADR-0023).
  Layout, images and fonts are not rendered; the original downloads via a presigned URL.
- **No folder re-parenting** and **no version restore** (a superseded version keeps its
  file for download, but its text is gone).
- **Backend gaps the UI works around:** no user directory (members are shown and invited
  by account ID, and an uploader is shown as "you" or a short member id), and no
  profile-edit or password-change endpoint. The upload policy (size, extensions) is exposed
  on `/meta`, so the UI states it up front.
- **Content-Security-Policy** is not set on HTML responses (the security doc specifies
  one). The code is written to be compatible — no inline scripts, self-hosted fonts —
  but wiring nonces is M8.
