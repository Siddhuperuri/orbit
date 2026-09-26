"use client";

import type { ReactNode } from "react";

import { ErrorState } from "@/components/feedback/error-state";
import { GridLines } from "@/components/layout/grid-lines";
import { Wordmark } from "@/components/layout/wordmark";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { useCurrentUser } from "@/features/auth/api/use-current-user";
import { UserProvider } from "@/features/auth/hooks/use-user";
import { isApiError } from "@/lib/api/errors";

/**
 * The authentication boundary for everything behind sign-in.
 *
 * It is a client-side gate, and that is forced by the cookie design rather than
 * chosen: both auth cookies are scoped to `/api` (ADR-0009), so the browser never
 * attaches them to a *page* request and a Server Component cannot see whether
 * anyone is signed in. What keeps this safe is that no private data is in the
 * page HTML -- the server renders only chrome, and every document, search result,
 * and message is fetched afterwards, by the API, which enforces authorization on
 * every request. The gate exists for experience (redirect promptly, never flash an
 * empty shell), not for protection.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const { data: user, error, refetch, isFetching } = useCurrentUser();

  if (user) return <UserProvider value={user}>{children}</UserProvider>;

  // A 401 has already been announced to the session handler, which is redirecting.
  if (error && !(isApiError(error) && error.requiresAuthentication)) {
    return (
      <div className="flex min-h-dvh items-center justify-center px-4">
        <ErrorState
          error={error}
          title="Couldn't load your account"
          onRetry={() => void refetch()}
          retrying={isFetching}
        />
      </div>
    );
  }

  // Shaped like the shell it becomes -- sidebar, ruled column -- so the page does not
  // jump from a centred logo to a full layout the moment the account resolves.
  return (
    <div role="status" className="grain bg-canvas flex h-dvh overflow-hidden">
      <span className="sr-only">Loading…</span>
      <div
        aria-hidden="true"
        className="border-line hidden w-64 shrink-0 flex-col border-r lg:flex"
      >
        <div className="border-line flex h-14 items-center border-b px-5">
          <Wordmark />
        </div>
        <div className="space-y-3 p-5">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="mt-4 h-6 w-4/5" />
          <Skeleton className="h-6 w-3/5" />
          <Skeleton className="h-6 w-2/3" />
        </div>
      </div>
      <div className="relative flex min-w-0 flex-1">
        <GridLines />
        <div className="relative flex flex-1 items-center justify-center">
          <span className="label-micro text-fg-subtle flex items-center gap-3" aria-hidden="true">
            <Spinner />
            Loading your workspace…
          </span>
        </div>
      </div>
    </div>
  );
}
