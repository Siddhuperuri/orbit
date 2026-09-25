/**
 * Every URL the application navigates to, in one place, and the one function that
 * decides whether a `?next=` value is safe to follow.
 *
 * Route builders are functions rather than string literals scattered through
 * components: a route that moves is then a one-line change, and a typo is a
 * compile error rather than a 404.
 */

export const routes = {
  home: "/",
  login: "/auth/login",
  register: "/auth/register",
  forgotPassword: "/auth/forgot-password",
  resetPassword: "/auth/reset-password",
  verifyEmail: "/auth/verify-email",
  account: "/account",
  newWorkspace: "/workspaces/new",

  workspace: (workspaceId: string) => `/workspaces/${workspaceId}`,
  documents: (workspaceId: string) => `/workspaces/${workspaceId}/documents`,
  /**
   * `passage` opens the document at that passage of its text, and `version` says which
   * version the link was made against, so a citation to text that has since been replaced
   * can say so instead of silently pointing at something else.
   */
  document: (
    workspaceId: string,
    documentId: string,
    target?: { passage?: number; version?: number },
  ) => {
    const params = new URLSearchParams();
    if (target?.passage !== undefined) params.set("passage", String(target.passage));
    if (target?.version !== undefined) params.set("v", String(target.version));
    const suffix = params.toString();
    return `/workspaces/${workspaceId}/documents/${documentId}${suffix ? `?${suffix}` : ""}`;
  },
  search: (workspaceId: string, query?: { q?: string; mode?: string; doc?: string }) => {
    const params = new URLSearchParams();
    if (query?.q) params.set("q", query.q);
    if (query?.mode) params.set("mode", query.mode);
    if (query?.doc) params.set("doc", query.doc);
    const suffix = params.toString();
    return `/workspaces/${workspaceId}/search${suffix ? `?${suffix}` : ""}`;
  },
  /**
   * A question is user content and stays out of URLs (history, logs, referrers);
   * only a document scope, which is an id, travels in the address.
   */
  chat: (workspaceId: string, query?: { doc?: string }) =>
    `/workspaces/${workspaceId}/chat${query?.doc ? `?doc=${encodeURIComponent(query.doc)}` : ""}`,
  conversation: (workspaceId: string, conversationId: string) =>
    `/workspaces/${workspaceId}/chat/${conversationId}`,
  workspaceSettings: (workspaceId: string) => `/workspaces/${workspaceId}/settings`,
  members: (workspaceId: string) => `/workspaces/${workspaceId}/settings/members`,
} as const;

const ORIGIN_PROBE = "http://orbit.invalid";

/**
 * Resolve a `?next=` parameter to a same-origin path, or fall back.
 *
 * Following an attacker-supplied `next` after login is an open redirect -- a
 * phishing link on a trusted domain. So the value must be a *relative path* that,
 * when resolved, stays on this origin. `//evil.example` and `/\evil.example` look
 * relative to a naive `startsWith("/")` check and are not; resolving against a
 * probe origin and comparing catches every such form, including ones with control
 * characters a browser would strip. Auth pages and the API are refused as
 * targets: bouncing back to `/auth/login` is a loop, and `/api` is not a page.
 */
export function safeNextPath(
  raw: string | null | undefined,
  fallback: string = routes.home,
): string {
  if (!raw) return fallback;
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) return fallback;

  let url: URL;
  try {
    url = new URL(raw, ORIGIN_PROBE);
  } catch {
    return fallback;
  }
  if (url.origin !== ORIGIN_PROBE) return fallback;
  if (url.pathname.startsWith("/auth") || url.pathname.startsWith("/api")) return fallback;

  const resolved = `${url.pathname}${url.search}${url.hash}`;
  // Normalisation can *create* a protocol-relative path: `/.//evil.example` and
  // `/..//evil.example` both resolve to the pathname `//evil.example`, which is on
  // this origin as far as the URL parser is concerned -- but handed to the router
  // as a redirect target it is read as a different host. Checking the raw input
  // above is not enough; the value that is *returned* must be checked too.
  if (resolved.startsWith("//") || resolved.startsWith("/\\")) return fallback;

  return resolved;
}

export type SignInReason = "expired" | "signed-out" | "reset" | "registered";

/** The sign-in URL, remembering where the user was and why they were sent there. */
export function loginPath(options: { next?: string; reason?: SignInReason } = {}): string {
  const params = new URLSearchParams();
  const next = safeNextPath(options.next, "");
  if (next && next !== routes.home) params.set("next", next);
  if (options.reason) params.set("reason", options.reason);
  const suffix = params.toString();
  return suffix ? `${routes.login}?${suffix}` : routes.login;
}
