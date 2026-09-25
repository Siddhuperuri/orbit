import { api } from "@/lib/api/client";

/** Conversation and message calls. The streaming ask lives in `stream.ts`. */
export const chatApi = {
  list: (
    workspaceId: string,
    params: { limit?: number; cursor?: string } = {},
    signal?: AbortSignal,
  ) =>
    api.get("/api/v1/workspaces/{workspace_id}/conversations", {
      path: { workspace_id: workspaceId },
      query: { limit: params.limit, cursor: params.cursor },
      signal,
    }),

  create: (workspaceId: string, title?: string) =>
    api.post("/api/v1/workspaces/{workspace_id}/conversations", {
      path: { workspace_id: workspaceId },
      json: title ? { title } : {},
    }),

  get: (workspaceId: string, conversationId: string, signal?: AbortSignal) =>
    api.get("/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}", {
      path: { workspace_id: workspaceId, conversation_id: conversationId },
      signal,
    }),

  remove: (workspaceId: string, conversationId: string) =>
    api.delete("/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}", {
      path: { workspace_id: workspaceId, conversation_id: conversationId },
    }),

  messages: (
    workspaceId: string,
    conversationId: string,
    params: { after?: number; limit?: number } = {},
    signal?: AbortSignal,
  ) =>
    api.get("/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/messages", {
      path: { workspace_id: workspaceId, conversation_id: conversationId },
      query: { after: params.after, limit: params.limit },
      signal,
    }),
};
