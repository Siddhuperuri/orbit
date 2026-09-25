"use client";

import type { ReactNode } from "react";

import { ErrorState } from "@/components/feedback/error-state";
import { StaleNotice } from "@/components/feedback/stale-notice";
import { useOnlineStatus } from "@/lib/hooks/use-online-status";

/** The slice of a TanStack query result this component reads; both `useQuery` and `useInfiniteQuery` satisfy it. */
export interface QueryLike<TData> {
  data: TData | undefined;
  error: unknown;
  isPending: boolean;
  isError: boolean;
  isRefetchError: boolean;
  isFetching: boolean;
  dataUpdatedAt: number;
  refetch: () => unknown;
}

/**
 * One rendering policy for server state, used by every screen that reads it:
 *
 *   pending                     -> the caller's skeleton (matches final layout)
 *   failed, nothing to show     -> an error with retry
 *   has data, refresh failed    -> the data, labelled stale, with retry
 *   has data, browser offline   -> the data, labelled stale
 *   has data, but empty         -> the caller's empty state
 *   otherwise                   -> the data
 *
 * Having this in one place is what stops each screen inventing its own subtly
 * different (and usually incomplete) handling of those cases.
 */
export function QueryBoundary<TData>({
  query,
  loading,
  empty,
  isEmpty,
  errorTitle,
  staleNotice = true,
  children,
}: {
  query: QueryLike<TData>;
  loading: ReactNode;
  empty?: ReactNode;
  isEmpty?: (data: TData) => boolean;
  errorTitle?: string;
  /**
   * Turn off the "couldn't refresh" label. For when the page has already said something more
   * specific about why -- a retry button on a document that no longer exists would only mislead.
   */
  staleNotice?: boolean;
  children: (data: TData) => ReactNode;
}) {
  const online = useOnlineStatus();
  const { data } = query;

  if (query.isPending) return <>{loading}</>;

  if (data === undefined) {
    return (
      <ErrorState
        error={query.error}
        title={errorTitle}
        onRetry={() => void query.refetch()}
        retrying={query.isFetching}
      />
    );
  }

  const stale = staleNotice && (query.isRefetchError || !online);
  const notice = stale ? (
    <StaleNotice
      updatedAt={query.dataUpdatedAt}
      offline={!online}
      onRetry={() => void query.refetch()}
      retrying={query.isFetching}
    />
  ) : null;

  if (isEmpty?.(data)) {
    return (
      <>
        {notice}
        {empty}
      </>
    );
  }

  return (
    <div aria-busy={query.isFetching || undefined}>
      {notice}
      {children(data)}
    </div>
  );
}
