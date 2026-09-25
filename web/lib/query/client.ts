import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

import { notify } from "@/components/feedback/notify";
import { workspaceKeys } from "@/features/workspaces/api/keys";
import { ErrorCode, isAbortError, isApiError } from "@/lib/api/errors";

/**
 * TanStack Query owns all application data (ADR-0016), so its defaults are a
 * product decision rather than boilerplate.
 */

declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: {
      /** The mutation reports its own failure (inline in a form); skip the toast. */
      handledLocally?: boolean;
      /** Names the failed action in the toast: "Couldn't rename document". */
      errorTitle?: string;
    };
  }
}

/** Attempts beyond the first, for requests that could plausibly succeed on retry. */
const MAX_RETRIES = 2;

/** Data is treated as fresh for this long before a background refetch. */
const STALE_TIME_MS = 30_000;

/** How long an unused query stays cached before eviction. */
const GC_TIME_MS = 5 * 60_000;

export function createQueryClient(): QueryClient {
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      onError: (error) => {
        // Query failures render where the user is looking (an error state, or a
        // stale notice over kept data); a toast would double-report them. What is
        // worth surfacing here is a *bug*: a thrown value that is not an API
        // error at all.
        if (!isApiError(error) && !isAbortError(error)) console.error(error);
      },
    }),

    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        // A 403 on something we offered means our idea of the caller's role is out of
        // date -- they were demoted, or removed, since this page loaded. Re-read it, so
        // the controls they can no longer use disappear instead of failing one by one.
        // This applies whether or not the caller reports the error itself.
        if (isApiError(error) && error.code === ErrorCode.PermissionDenied) {
          void client.invalidateQueries({ queryKey: workspaceKeys.all });
        }
        if (mutation.meta?.handledLocally || isAbortError(error)) return;
        // A 401 has already ended the session and redirected; a toast on top of
        // the sign-in page would be noise.
        if (isApiError(error) && error.requiresAuthentication) return;
        notify.error(error, { title: mutation.meta?.errorTitle });
      },
    }),

    defaultOptions: {
      queries: {
        staleTime: STALE_TIME_MS,
        gcTime: GC_TIME_MS,

        // Retrying a 404 or a 403 is pointless and hides the real error behind
        // seconds of spinner. Only genuinely transient failures are retried.
        retry: (failureCount, error) => {
          if (failureCount >= MAX_RETRIES) return false;
          if (isApiError(error)) return error.isRetryable;
          return false;
        },
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),

        // Refetching on every window focus is a common default that generates
        // constant background load for data that rarely changes underneath the
        // user. Views that need liveness poll explicitly instead.
        refetchOnWindowFocus: false,
        // Reconnecting after being offline is a genuine reason to refetch.
        refetchOnReconnect: true,
      },
      mutations: {
        // A mutation is not idempotent in general; retrying one automatically
        // risks duplicating a side effect. Retries are opt-in per mutation.
        retry: false,
      },
    },
  });
  return client;
}
