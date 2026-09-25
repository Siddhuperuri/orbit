import { workspaceKeys } from "@/features/workspaces/api/keys";

export const tagKeys = {
  list: (workspaceId: string) => [...workspaceKeys.scope(workspaceId), "tags"] as const,
};
