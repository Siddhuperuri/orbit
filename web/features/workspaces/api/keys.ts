/**
 * Every query for a workspace lives under `['workspaces', id, ...]`, so deleting a
 * workspace can drop everything about it with one `removeQueries`, and an upload
 * can invalidate exactly one workspace's documents (ADR-0016).
 */
export const workspaceKeys = {
  all: ["workspaces"] as const,
  list: () => [...workspaceKeys.all, "list"] as const,
  scope: (workspaceId: string) => [...workspaceKeys.all, workspaceId] as const,
  detail: (workspaceId: string) => [...workspaceKeys.scope(workspaceId), "detail"] as const,
  members: (workspaceId: string) => [...workspaceKeys.scope(workspaceId), "members"] as const,
};
