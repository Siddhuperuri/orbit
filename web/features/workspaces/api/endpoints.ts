import { api } from "@/lib/api/client";
import type { Role } from "@/features/workspaces/types";

/** Every workspace and membership call. No component builds a URL. */
export const workspaceApi = {
  list: (signal?: AbortSignal) => api.get("/api/v1/workspaces", { signal }),

  get: (workspaceId: string, signal?: AbortSignal) =>
    api.get("/api/v1/workspaces/{workspace_id}", { path: { workspace_id: workspaceId }, signal }),

  create: (name: string) => api.post("/api/v1/workspaces", { json: { name } }),

  /** `expectedVersion` is optimistic concurrency: a stale write is refused with 409. */
  rename: (workspaceId: string, name: string, expectedVersion: number) =>
    api.patch("/api/v1/workspaces/{workspace_id}", {
      path: { workspace_id: workspaceId },
      json: { name, expected_version: expectedVersion },
    }),

  remove: (workspaceId: string) =>
    api.delete("/api/v1/workspaces/{workspace_id}", { path: { workspace_id: workspaceId } }),

  members: (workspaceId: string, signal?: AbortSignal) =>
    api.get("/api/v1/workspaces/{workspace_id}/members", {
      path: { workspace_id: workspaceId },
      signal,
    }),

  invite: (workspaceId: string, userId: string, role: Role) =>
    api.post("/api/v1/workspaces/{workspace_id}/members", {
      path: { workspace_id: workspaceId },
      json: { user_id: userId, role },
    }),

  changeRole: (workspaceId: string, userId: string, role: Role) =>
    api.patch("/api/v1/workspaces/{workspace_id}/members/{user_id}", {
      path: { workspace_id: workspaceId, user_id: userId },
      json: { role },
    }),

  removeMember: (workspaceId: string, userId: string) =>
    api.delete("/api/v1/workspaces/{workspace_id}/members/{user_id}", {
      path: { workspace_id: workspaceId, user_id: userId },
    }),
};
