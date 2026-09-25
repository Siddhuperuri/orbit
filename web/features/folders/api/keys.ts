import { workspaceKeys } from "@/features/workspaces/api/keys";

export const folderKeys = {
  list: (workspaceId: string) => [...workspaceKeys.scope(workspaceId), "folders"] as const,
};
