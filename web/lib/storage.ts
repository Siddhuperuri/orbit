/**
 * Guarded `localStorage`. Storage can throw (private browsing, blocked site data,
 * quota) and is absent during server rendering, so every access goes through
 * here. Nothing important is kept in it: it holds per-viewer conveniences only
 * (theme, last workspace), never data the app depends on.
 */

export function readStorage(key: string): string | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeStorage(key: string, value: string | null): void {
  try {
    if (typeof window === "undefined") return;
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // A convenience that could not be saved is not worth interrupting the user.
  }
}

export const StorageKey = {
  theme: "orbit:theme",
  lastWorkspace: "orbit:last-workspace",
} as const;
