/**
 * Query keys are declared here, never inline at a call site. An inline key that
 * differs by one character is a cache miss nobody notices until stale data ships
 * (ADR-0016).
 */
export const authKeys = {
  all: ["auth"] as const,
  me: () => [...authKeys.all, "me"] as const,
};
