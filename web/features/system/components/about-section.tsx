"use client";

import { QueryBoundary } from "@/components/feedback/query-boundary";
import { Skeleton } from "@/components/ui/skeleton";
import { useServiceMeta } from "@/features/system/api/use-service-meta";

/**
 * Which build the API is -- the first thing a support conversation needs. Also a
 * cheap live check that the browser, the single-origin proxy, and the API agree.
 */
export function AboutSection() {
  const query = useServiceMeta();

  return (
    <QueryBoundary
      query={query}
      loading={<Skeleton className="h-16 max-w-sm" />}
      errorTitle="Couldn't reach the API"
    >
      {(meta) => (
        <dl className="grid max-w-sm grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-base">
          <dt className="text-fg-muted">Service</dt>
          <dd className="font-mono text-sm">{meta.service}</dd>
          <dt className="text-fg-muted">Version</dt>
          <dd className="font-mono text-sm">{meta.version}</dd>
          <dt className="text-fg-muted">API</dt>
          <dd className="font-mono text-sm">{meta.api_version}</dd>
          <dt className="text-fg-muted">Environment</dt>
          <dd className="font-mono text-sm">{meta.environment}</dd>
        </dl>
      )}
    </QueryBoundary>
  );
}
