import { useSyncExternalStore } from "react";
import { vi } from "vitest";

/**
 * A stand-in for `next/navigation` (registered in `setup.ts`) that keeps a real URL.
 *
 * Components in this app treat the address bar as state -- the documents page parses its
 * filters out of `useSearchParams()` and writes them back with `router.replace()` -- so a mock
 * that merely records calls cannot test them: nothing would ever re-render with the new URL.
 * This one applies `push`/`replace` to a URL and notifies subscribers, so the round trip is the
 * real one, and tests assert on `navigation.url` (what the address bar would say) rather than
 * on how it got there.
 */

const ORIGIN = "http://localhost";

let current = new URL("/", ORIGIN);
let params = new URLSearchParams(current.search);
const listeners = new Set<() => void>();

function go(href: string) {
  current = new URL(href, current);
  params = new URLSearchParams(current.search);
  for (const listener of listeners) listener();
}

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

const router = {
  push: vi.fn((href: string) => go(href)),
  replace: vi.fn((href: string) => go(href)),
  back: vi.fn(),
  forward: vi.fn(),
  refresh: vi.fn(),
  prefetch: vi.fn(),
};

export const navigation = {
  router,
  /** Where the address bar is: path and query. */
  get url() {
    return current.pathname + current.search;
  },
  /** Start a test at `href`, with no recorded navigations. */
  reset(href = "/") {
    current = new URL(href, ORIGIN);
    params = new URLSearchParams(current.search);
    for (const fn of Object.values(router)) fn.mockClear();
  },
};

export function useRouter() {
  return router;
}

export function usePathname() {
  return useSyncExternalStore(
    subscribe,
    () => current.pathname,
    () => current.pathname,
  );
}

export function useSearchParams() {
  return useSyncExternalStore(
    subscribe,
    () => params,
    () => params,
  );
}
