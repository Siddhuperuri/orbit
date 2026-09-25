"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { chatApi } from "@/features/chat/api/endpoints";
import { chatKeys } from "@/features/chat/api/keys";
import type { Message } from "@/features/chat/types";

const PAGE_SIZE = 25;
const MESSAGE_PAGE_SIZE = 100;
/** A thread of 1,000 messages is far past what one conversation should hold; this bounds the loop, not the product. */
const MAX_MESSAGE_PAGES = 10;

export function useConversations(workspaceId: string) {
  return useInfiniteQuery({
    queryKey: chatKeys.lists(workspaceId),
    queryFn: ({ pageParam, signal }) =>
      chatApi.list(workspaceId, { limit: PAGE_SIZE, cursor: pageParam }, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
}

export function useConversation(workspaceId: string, conversationId: string) {
  return useQuery({
    queryKey: chatKeys.detail(workspaceId, conversationId),
    queryFn: ({ signal }) => chatApi.get(workspaceId, conversationId, signal),
  });
}

/**
 * A conversation's whole thread, in order. The API pages messages by ordinal
 * (`next_after`); a thread is read front to back, so the pages are followed until
 * the end and returned as one array -- which is also what lets a finished streamed
 * answer be appended to the cache in a single, simple write.
 */
export function useMessages(workspaceId: string, conversationId: string, enabled = true) {
  return useQuery({
    enabled,
    queryKey: chatKeys.messages(workspaceId, conversationId),
    queryFn: async ({ signal }) => {
      const messages: Message[] = [];
      let after: number | undefined;
      for (let page = 0; page < MAX_MESSAGE_PAGES; page += 1) {
        const result = await chatApi.messages(
          workspaceId,
          conversationId,
          { after, limit: MESSAGE_PAGE_SIZE },
          signal,
        );
        messages.push(...result.items);
        if (result.next_after === null) break;
        after = result.next_after;
      }
      return messages;
    },
  });
}

export function useCreateConversation(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (title?: string) => chatApi.create(workspaceId, title),
    meta: { errorTitle: "Couldn't start a conversation" },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: chatKeys.lists(workspaceId) }),
  });
}

export function useDeleteConversation(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (conversationId: string) => chatApi.remove(workspaceId, conversationId),
    meta: { handledLocally: true },
    onSuccess: (_result, conversationId) => {
      queryClient.removeQueries({ queryKey: chatKeys.detail(workspaceId, conversationId) });
      return queryClient.invalidateQueries({ queryKey: chatKeys.lists(workspaceId) });
    },
  });
}
