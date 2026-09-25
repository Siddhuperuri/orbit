import { workspaceKeys } from "@/features/workspaces/api/keys";

export const chatKeys = {
  all: (workspaceId: string) => [...workspaceKeys.scope(workspaceId), "conversations"] as const,
  lists: (workspaceId: string) => [...chatKeys.all(workspaceId), "list"] as const,
  detail: (workspaceId: string, conversationId: string) =>
    [...chatKeys.all(workspaceId), "detail", conversationId] as const,
  messages: (workspaceId: string, conversationId: string) =>
    [...chatKeys.detail(workspaceId, conversationId), "messages"] as const,
};
