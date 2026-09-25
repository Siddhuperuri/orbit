import { api } from "@/lib/api/client";
import type { Schema } from "@/lib/api/types";

/**
 * Auth endpoints. Every API call for this feature is here; components and hooks
 * never call `fetch` or build a URL.
 *
 * The calls that establish or end a session pass `refresh: false`: a rejected
 * login is a wrong password, not an expired session, and must not trigger the
 * transparent token refresh.
 */

export type LoginBody = Schema<"LoginRequest">;
export type RegisterBody = Schema<"RegisterRequest">;

export const authApi = {
  me: (signal?: AbortSignal) => api.get("/api/v1/auth/me", { signal }),

  login: (body: LoginBody) => api.post("/api/v1/auth/login", { json: body, refresh: false }),

  register: (body: RegisterBody) =>
    api.post("/api/v1/auth/register", { json: body, refresh: false }),

  logout: () => api.post("/api/v1/auth/logout", { refresh: false }),

  requestPasswordReset: (email: string) =>
    api.post("/api/v1/auth/password-reset", { json: { email }, refresh: false }),

  confirmPasswordReset: (token: string, newPassword: string) =>
    api.post("/api/v1/auth/password-reset/confirm", {
      json: { token, new_password: newPassword },
      refresh: false,
    }),

  /** Needs a session; a stale access token is refreshed transparently. */
  resendVerification: () => api.post("/api/v1/auth/verify-email/resend"),

  confirmEmail: (token: string) =>
    api.post("/api/v1/auth/verify-email/confirm", { json: { token }, refresh: false }),
};
