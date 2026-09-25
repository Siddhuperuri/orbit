import { api } from "@/lib/api/client";

export const folderApi = {
  list: (workspaceId: string, signal?: AbortSignal) =>
    api.get("/api/v1/workspaces/{workspace_id}/folders", {
      path: { workspace_id: workspaceId },
      signal,
    }),

  create: (workspaceId: string, name: string, parentId: string | null) =>
    api.post("/api/v1/workspaces/{workspace_id}/folders", {
      path: { workspace_id: workspaceId },
      json: { name, parent_id: parentId },
    }),

  rename: (workspaceId: string, folderId: string, name: string, expectedVersion: number) =>
    api.patch("/api/v1/workspaces/{workspace_id}/folders/{folder_id}", {
      path: { workspace_id: workspaceId, folder_id: folderId },
      json: { name, expected_version: expectedVersion },
    }),

  remove: (workspaceId: string, folderId: string) =>
    api.delete("/api/v1/workspaces/{workspace_id}/folders/{folder_id}", {
      path: { workspace_id: workspaceId, folder_id: folderId },
    }),
};
