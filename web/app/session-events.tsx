"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import {
  announceSessionEnded,
  hasHadSession,
  listenForSignOut,
  onSessionEnded,
} from "@/lib/api/session";
import { loginPath } from "@/lib/navigation";

/**
 * Reacts to the session ending -- once, for the whole application.
 *
 * The API layer decides *that* the session is over (a refresh failed, another tab
 * signed out) and announces it; this decides what the app does about it: forget
 * every cached byte of private data, and send the user to sign in, remembering
 * where they were. It is implemented here once instead of in each query, per
 * ADR-0016.
 */
export function SessionEvents() {
  const router = useRouter();
  const queryClient = useQueryClient();

  useEffect(() => {
    return onSessionEnded((reason) => {
      // The next user of this browser must not see the previous user's data in
      // a cache, so the cache goes before anything else.
      queryClient.clear();

      const { pathname, search } = window.location;
      if (pathname.startsWith("/auth")) return;

      router.replace(
        loginPath({
          next: `${pathname}${search}`,
          // Someone who never signed in, arriving cold, is not "expired".
          reason: hasHadSession() || reason === "signed-out" ? reason : undefined,
        }),
      );
    });
  }, [queryClient, router]);

  useEffect(() => listenForSignOut(() => announceSessionEnded("signed-out")), []);

  return null;
}
