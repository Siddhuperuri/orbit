import type { Document, ProcessingStatus } from "@/features/documents/types";

/**
 * A document's state, from its current version. `READY` and `FAILED` are terminal:
 * nothing will change them without user action, so polling stops there
 * (ADR-0016: polling is scoped and self-terminating).
 */

export const TERMINAL_STATUSES: readonly ProcessingStatus[] = ["ready", "failed"];

export function isTerminalStatus(status: ProcessingStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

/** The status of a document's current version. A document with none is treated as queued. */
export function statusOf(document: Document): ProcessingStatus {
  return document.current_version?.status ?? "pending";
}

export function isInProgress(document: Document): boolean {
  return !isTerminalStatus(statusOf(document));
}

export const STATUS_LABELS: Record<ProcessingStatus, string> = {
  pending: "Queued",
  processing: "Processing",
  ready: "Ready",
  failed: "Failed",
};

/** What each state means, for a tooltip or a screen reader. */
export const STATUS_DESCRIPTIONS: Record<ProcessingStatus, string> = {
  pending: "Waiting for a worker to pick it up.",
  processing: "Being read, split into passages, and indexed.",
  ready: "Indexed. It can be searched and cited in answers.",
  failed: "Processing didn't complete. It isn't searchable.",
};
