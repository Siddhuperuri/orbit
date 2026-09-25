/**
 * Poll intervals for work that finishes on its own (document processing).
 *
 * Backoff rather than a fixed interval: a document usually finishes within a few
 * seconds, so the first checks are quick, but one stuck behind a queue should not
 * cost the API a request every two seconds for as long as the tab is open
 * (ADR-0016: polling is scoped and self-terminating).
 */

const BASE_MS = 1_500;
const CEILING_MS = 15_000;
const GROWTH = 1.6;

/** Delay before poll number `attempt` (0-based). 1.5s, 2.4s, 3.8s ... capped at 15s. */
export function pollDelay(attempt: number): number {
  return Math.min(CEILING_MS, Math.round(BASE_MS * GROWTH ** Math.max(0, attempt)));
}
