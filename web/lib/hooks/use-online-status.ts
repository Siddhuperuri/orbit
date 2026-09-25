"use client";

import { useSyncExternalStore } from "react";

/**
 * Whether the browser believes it has a network connection. `navigator.onLine`
 * can say "online" on a dead link, so this is a hint used to *label* stale data,
 * never a gate on making requests.
 */
export function useOnlineStatus(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      window.addEventListener("online", onChange);
      window.addEventListener("offline", onChange);
      return () => {
        window.removeEventListener("online", onChange);
        window.removeEventListener("offline", onChange);
      };
    },
    () => navigator.onLine,
    () => true,
  );
}
