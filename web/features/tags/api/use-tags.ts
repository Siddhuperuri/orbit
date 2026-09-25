"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { documentKeys } from "@/features/documents/api/keys";
import { tagApi } from "@/features/tags/api/endpoints";
import { tagKeys } from "@/features/tags/api/keys";
import type { TagColor } from "@/features/tags/types";
import { ErrorCode, isApiError } from "@/lib/api/errors";

export function useTags(workspaceId: string) {
  return useQuery({
    queryKey: tagKeys.list(workspaceId),
    queryFn: ({ signal }) => tagApi.list(workspaceId, signal),
    select: (data) => data.items,
    refetchOnWindowFocus: true,
  });
}

/** A tag changed or vanished: the documents that wear it (chips, filters) are out of date too. */
function useRefreshDocuments(workspaceId: string) {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: tagKeys.list(workspaceId) }),
      queryClient.invalidateQueries({ queryKey: documentKeys.all(workspaceId) }),
    ]);
}

export function useCreateTag(workspaceId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ name, color }: { name: string; color: TagColor }) =>
      tagApi.create(workspaceId, name, color),
    meta: { handledLocally: true },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: tagKeys.list(workspaceId) }),
  });
}

export function useUpdateTag(workspaceId: string) {
  const queryClient = useQueryClient();
  const refreshDocuments = useRefreshDocuments(workspaceId);

  return useMutation({
    mutationFn: ({
      tagId,
      changes,
      expectedVersion,
    }: {
      tagId: string;
      changes: { name?: string; color?: TagColor };
      expectedVersion: number;
    }) => tagApi.update(workspaceId, tagId, changes, expectedVersion),
    meta: { handledLocally: true },
    onSuccess: () => refreshDocuments(),
    onError: (error) => {
      if (isApiError(error) && (error.status === 409 || error.code === ErrorCode.NotFound)) {
        void queryClient.invalidateQueries({ queryKey: tagKeys.list(workspaceId) });
      }
    },
  });
}

/** Deleting a tag that is already gone is what was asked for, so a 404 is a success. */
export function useDeleteTag(workspaceId: string) {
  const refreshDocuments = useRefreshDocuments(workspaceId);

  return useMutation({
    mutationFn: async (tagId: string) => {
      try {
        await tagApi.remove(workspaceId, tagId);
      } catch (error) {
        if (isApiError(error) && error.code === ErrorCode.NotFound) return;
        throw error;
      }
    },
    meta: { handledLocally: true },
    onSuccess: () => refreshDocuments(),
  });
}
