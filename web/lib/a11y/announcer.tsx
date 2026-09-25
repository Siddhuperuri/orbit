"use client";

import { useSyncExternalStore } from "react";

/**
 * A polite/assertive live region for status changes a sighted user notices
 * passively and a screen-reader user otherwise would not: a document finishing
 * processing, a search returning, an upload completing.
 *
 * One region, mounted once at the root, addressed through `announce()` from
 * anywhere -- including non-React code such as a query-cache callback. Regions
 * must exist in the DOM *before* their content changes to be announced reliably,
 * which is why this is not created on demand.
 */

type Politeness = "polite" | "assertive";

interface Announcement {
  id: number;
  message: string;
  politeness: Politeness;
}

const CLEAR_AFTER_MS = 4_000;
const listeners = new Set<() => void>();
let current: Announcement | null = null;
let counter = 0;
let clearTimer: ReturnType<typeof setTimeout> | undefined;

function emit() {
  for (const listener of listeners) listener();
}

export function announce(message: string, politeness: Politeness = "polite"): void {
  counter += 1;
  current = { id: counter, message, politeness };
  emit();
  // Left in place, stale text would be re-read when a screen-reader user browses
  // the page later.
  clearTimeout(clearTimer);
  clearTimer = setTimeout(() => {
    current = null;
    emit();
  }, CLEAR_AFTER_MS);
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

const getSnapshot = () => current;
const getServerSnapshot = () => null;

export function Announcer() {
  const announcement = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const show = (politeness: Politeness) =>
    announcement?.politeness === politeness ? (
      // A fresh key per announcement makes an identical repeated message re-announce.
      <span key={announcement.id}>{announcement.message}</span>
    ) : null;

  return (
    <>
      <div role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {show("polite")}
      </div>
      <div role="alert" aria-live="assertive" aria-atomic="true" className="sr-only">
        {show("assertive")}
      </div>
    </>
  );
}
