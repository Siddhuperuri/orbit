"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { authApi, type LoginBody, type RegisterBody } from "@/features/auth/api/endpoints";
import { authKeys } from "@/features/auth/api/keys";
import { isApiError } from "@/lib/api/errors";
import { announceSessionEnded, broadcastSignOut, markSessionActive } from "@/lib/api/session";

/**
 * Auth mutations. Forms show their own errors inline, so these opt out of the
 * global failure toast (`handledLocally`).
 */

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: LoginBody) => authApi.login(body),
    meta: { handledLocally: true },
    onSuccess: ({ user }) => {
      // Whatever was cached belonged to whoever used this browser before.
      queryClient.clear();
      markSessionActive();
      queryClient.setQueryData(authKeys.me(), user);
    },
  });
}

export function useRegister() {
  return useMutation({
    mutationFn: (body: RegisterBody) => authApi.register(body),
    meta: { handledLocally: true },
  });
}

export function useLogout() {
  return useMutation({
    mutationFn: async () => {
      try {
        await authApi.logout();
      } catch (error) {
        // Logout is idempotent: a 401 means the session was already gone, which
        // is the state the user asked for.
        if (!isApiError(error) || !error.requiresAuthentication) throw error;
      }
    },
    meta: { errorTitle: "Couldn't sign out" },
    onSuccess: () => {
      broadcastSignOut();
      // One path for "the session is over": clears the cache and redirects.
      announceSessionEnded("signed-out");
    },
  });
}

export function useRequestPasswordReset() {
  return useMutation({
    mutationFn: (email: string) => authApi.requestPasswordReset(email),
    meta: { handledLocally: true },
  });
}

export function useConfirmPasswordReset() {
  return useMutation({
    mutationFn: ({ token, newPassword }: { token: string; newPassword: string }) =>
      authApi.confirmPasswordReset(token, newPassword),
    meta: { handledLocally: true },
  });
}

export function useResendVerification() {
  return useMutation({
    mutationFn: () => authApi.resendVerification(),
    meta: { errorTitle: "Couldn't send the verification email" },
  });
}

export function useConfirmEmail() {
  return useMutation({
    mutationFn: (token: string) => authApi.confirmEmail(token),
    meta: { handledLocally: true },
  });
}
