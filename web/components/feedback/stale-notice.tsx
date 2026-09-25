"use client";

import { RefreshCw, WifiOff } from "lucide-react";

import { Button } from "@/components/ui/button";
import { formatRelativeTime } from "@/lib/utils/format";

/**
 * Labels data that may be out of date, instead of silently presenting it as
 * current. Shown when a background refresh failed, or the browser is offline;
 * the data underneath stays visible and usable.
 */
export function StaleNotice({
  updatedAt,
  offline,
  onRetry,
  retrying,
}: {
  /** `dataUpdatedAt` from the query: epoch milliseconds. */
  updatedAt: number;
  offline: boolean;
  onRetry: () => void;
  retrying: boolean;
}) {
  const Icon = offline ? WifiOff : RefreshCw;
  const when = updatedAt > 0 ? formatRelativeTime(new Date(updatedAt).toISOString()) : "earlier";

  return (
    <div
      role="status"
      className="border-warning/30 bg-warning-soft text-warning mb-4 flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-md border px-3 py-2 text-sm"
    >
      <Icon className="size-4 shrink-0" aria-hidden="true" />
      <p className="min-w-0 flex-1">
        {offline ? "You're offline. " : "Couldn't refresh. "}
        Showing what was loaded {when}.
      </p>
      <Button size="sm" variant="secondary" onClick={onRetry} loading={retrying}>
        {retrying ? "Retrying" : "Retry"}
      </Button>
    </div>
  );
}
