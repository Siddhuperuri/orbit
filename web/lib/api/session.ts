import { ErrorCode, isApiError, networkError, toApiError } from "@/lib/api/errors";

/**
 * Session lifecycle: transparent refresh, and telling the app when it is over.
 *
 * Both tokens are HttpOnly cookies (ADR-0009), so the browser -- not this code --
 * holds and sends them. What this module owns is the one operation that needs
 * care: **rotating the refresh token without tripping reuse detection.**
 *
 * The access token lives 15 minutes; after that the next request answers 401 and
 * the client must call `/auth/refresh`. Refresh tokens rotate, and presenting an
 * already-consumed one revokes the whole session family (ADR-0003). So two
 * requests that both notice the expiry and both refresh would log the user out --
 * and the same is true of two *tabs*, which share a cookie jar. This module
 * therefore guarantees that at most one refresh is in flight:
 *
 *   - within a tab, concurrent callers share a single promise;
 *   - across tabs, a Web Lock serialises them, and a timestamp in localStorage
 *     lets the second tab see that a refresh already happened after its own
 *     request was sent, and skip refreshing altogether.
 */

const REFRESH_URL = "/api/v1/auth/refresh";
const LOCK_NAME = "orbit:session-refresh";
const REFRESHED_AT_KEY = "orbit:session:refreshed-at";
const CHANNEL_NAME = "orbit:session";

export type RefreshOutcome = "refreshed" | "expired";

export type SessionEndReason = "expired" | "signed-out";

type SessionEndListener = (reason: SessionEndReason) => void;

const listeners = new Set<SessionEndListener>();
let inFlight: Promise<RefreshOutcome> | null = null;
let endedAndAnnounced = false;
let hadSession = false;
let memoryRefreshedAt = 0;

// ---------------------------------------------------------------------------
// Refresh
// ---------------------------------------------------------------------------

function readRefreshedAt(): number {
  try {
    const stored = window.localStorage.getItem(REFRESHED_AT_KEY);
    return Math.max(memoryRefreshedAt, stored ? Number(stored) || 0 : 0);
  } catch {
    // Storage can be blocked (private mode, policy); the in-memory value still
    // protects a single tab.
    return memoryRefreshedAt;
  }
}

function writeRefreshedAt(timestamp: number): void {
  memoryRefreshedAt = timestamp;
  try {
    window.localStorage.setItem(REFRESHED_AT_KEY, String(timestamp));
  } catch {
    // See readRefreshedAt.
  }
}

async function withCrossTabLock<T>(task: () => Promise<T>): Promise<T> {
  if (typeof navigator !== "undefined" && navigator.locks) {
    return navigator.locks.request(LOCK_NAME, task);
  }
  return task();
}

async function callRefreshEndpoint(): Promise<RefreshOutcome> {
  let response: Response;
  try {
    response = await fetch(REFRESH_URL, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json", "X-Requested-With": "orbit-web" },
    });
  } catch (cause) {
    throw networkError(cause);
  }

  if (response.ok) {
    writeRefreshedAt(Date.now());
    return "refreshed";
  }

  const error = await toApiError(response);
  // A 401 here means the refresh token is missing, expired, or revoked: the
  // session is over. Anything else (5xx, 429, network) is transient and must not
  // sign the user out -- it surfaces as a retryable error on the original call.
  if (error.requiresAuthentication) return "expired";
  throw error;
}

/**
 * Refresh the session, unless someone already has.
 *
 * @param requestStartedAt When the failing request was *sent*. If a refresh
 *   finished after that, the request failed on the old cookie and simply needs
 *   replaying -- refreshing again would present a consumed token.
 */
export function refreshSession(requestStartedAt: number): Promise<RefreshOutcome> {
  inFlight ??= withCrossTabLock(async () => {
    if (readRefreshedAt() > requestStartedAt) return "refreshed" as const;
    return callRefreshEndpoint();
  }).finally(() => {
    inFlight = null;
  });
  return inFlight;
}

/**
 * Run a request; if it fails because the session expired, refresh once and
 * replay it. Used by every transport (fetch, upload, stream) so the policy lives
 * in one place.
 */
export async function withSessionRefresh<T>(attempt: () => Promise<T>): Promise<T> {
  const startedAt = Date.now();
  try {
    return await attempt();
  } catch (error) {
    if (!isApiError(error) || error.code !== ErrorCode.AuthenticationRequired) throw error;

    const outcome = await refreshSession(startedAt);
    if (outcome === "expired") {
      announceSessionEnded("expired");
      throw error;
    }

    try {
      return await attempt();
    } catch (retryError) {
      // Refreshed, and still refused: the account is no longer valid (password
      // reset, membership change bumping the token epoch). Treat as ended.
      if (isApiError(retryError) && retryError.code === ErrorCode.AuthenticationRequired) {
        announceSessionEnded("expired");
      }
      throw retryError;
    }
  }
}

// ---------------------------------------------------------------------------
// Session end
// ---------------------------------------------------------------------------

/** Called once the app has confirmed a signed-in user (login, or `/auth/me`). */
export function markSessionActive(): void {
  hadSession = true;
  endedAndAnnounced = false;
}

/** Whether this tab has held a session -- distinguishes "expired" from "never signed in". */
export function hasHadSession(): boolean {
  return hadSession;
}

/** Subscribe to the session ending, for any reason. Returns an unsubscribe. */
export function onSessionEnded(listener: SessionEndListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Tell the app the session is over. Idempotent until the next login: a burst of
 * failing requests must produce one redirect, not a dozen.
 */
export function announceSessionEnded(reason: SessionEndReason): void {
  if (endedAndAnnounced) return;
  endedAndAnnounced = true;
  for (const listener of listeners) listener(reason);
}

let channel: BroadcastChannel | null = null;

function getChannel(): BroadcastChannel | null {
  if (typeof BroadcastChannel === "undefined") return null;
  channel ??= new BroadcastChannel(CHANNEL_NAME);
  return channel;
}

/** Tell other tabs this user signed out, so they do not sit on stale private data. */
export function broadcastSignOut(): void {
  getChannel()?.postMessage({ type: "signed-out" });
}

/** Listen for another tab signing out. Returns an unsubscribe. */
export function listenForSignOut(onSignOut: () => void): () => void {
  const target = getChannel();
  if (!target) return () => undefined;
  const handler = (event: MessageEvent<{ type?: string }>) => {
    if (event.data?.type === "signed-out") onSignOut();
  };
  target.addEventListener("message", handler);
  return () => target.removeEventListener("message", handler);
}

/** Test seam: forget module state between tests. */
export function resetSessionStateForTests(): void {
  inFlight = null;
  endedAndAnnounced = false;
  hadSession = false;
  memoryRefreshedAt = 0;
  listeners.clear();
}
