"use client";

import type { ReactNode } from "react";

import { ErrorState } from "@/components/feedback/error-state";
import { Wordmark } from "@/components/layout/wordmark";
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

  return (
    <div
      role="status"
      className="text-fg-muted flex min-h-dvh flex-col items-center justify-center gap-4"
    >
      <Wordmark />
      <span className="flex items-center gap-2 text-sm">
        <Spinner />
        Loading…
      </span>
    </div>
  );
}
