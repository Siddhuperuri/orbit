"use client";

import { useQuery } from "@tanstack/react-query";

import { authApi } from "@/features/auth/api/endpoints";
import { authKeys } from "@/features/auth/api/keys";
import { markSessionActive } from "@/lib/api/session";

/**
 * The signed-in account. This query *is* the authentication gate: if it cannot
 * produce a user, the shell does not render.
 *
 * A 401 is not retried here -- the API layer has already tried a token refresh and
 * announced the session's end, so retrying would only delay the redirect.
 */
export function useCurrentUser() {
  return useQuery({
    queryKey: authKeys.me(),
    queryFn: async ({ signal }) => {
      const user = await authApi.me(signal);
      markSessionActive();
      return user;
    },
    staleTime: 5 * 60_000,
  });
}
