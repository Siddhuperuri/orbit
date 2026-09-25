"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";

/**
 * Query keys are declared here, never inline at a call site.
 *
 * An inline key that differs by one character is a cache miss nobody notices
 * until stale data ships (ADR-0016).
 */
export const systemKeys = {
  all: ["system"] as const,
  meta: () => [...systemKeys.all, "meta"] as const,
};

/**
 * Build identity from the backend. Answers "which version am I on?" in a support
 * conversation -- and is a cheap end-to-end check that the single-origin proxy, the
 * client, and the error envelope are wired together.
 */
export function useServiceMeta() {
  return useQuery({
    queryKey: systemKeys.meta(),
    queryFn: ({ signal }) => api.get("/api/v1/meta", { signal }),
    // Build identity changes only on deploy, so there is no reason to refetch it.
    staleTime: Number.POSITIVE_INFINITY,
  });
}
