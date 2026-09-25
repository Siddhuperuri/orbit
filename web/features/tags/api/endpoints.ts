import { api } from "@/lib/api/client";
import type { TagColor } from "@/features/tags/types";

export const tagApi = {
  list: (workspaceId: string, signal?: AbortSignal) =>
    api.get("/api/v1/workspaces/{workspace_id}/tags", {
      path: { workspace_id: workspaceId },
      signal,
    }),

  create: (workspaceId: string, name: string, color: TagColor) =>
    api.post("/api/v1/workspaces/{workspace_id}/tags", {
      path: { workspace_id: workspaceId },
      json: { name, color },
    }),

  update: (
    workspaceId: string,
    tagId: string,
    changes: { name?: string; color?: TagColor },
    expectedVersion: number,
  ) =>
    api.patch("/api/v1/workspaces/{workspace_id}/tags/{tag_id}", {
      path: { workspace_id: workspaceId, tag_id: tagId },
      json: { ...changes, expected_version: expectedVersion },
    }),

  remove: (workspaceId: string, tagId: string) =>
    api.delete("/api/v1/workspaces/{workspace_id}/tags/{tag_id}", {
      path: { workspace_id: workspaceId, tag_id: tagId },
    }),
};
