"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { documentKeys } from "@/features/documents/api/keys";
import { folderApi } from "@/features/folders/api/endpoints";
import { folderKeys } from "@/features/folders/api/keys";
import { ErrorCode, isApiError } from "@/lib/api/errors";

/**
 * The whole tree, in one request: a workspace's folders are bounded (the API caps
 * them), and a tree cannot be paged. Refetched on focus, because folders are shared and
 * the counts on them change whenever anyone uploads or archives.
 */
export function useFolders(workspaceId: string) {
  return useQuery({
    queryKey: folderKeys.list(workspaceId),
    queryFn: ({ signal }) => folderApi.list(workspaceId, signal),
    select: (data) => data.items,
    refetchOnWindowFocus: true,
  });
}

/**
 * The folder mutations report failure inline, inside the dialog that made them (a name
 * already taken is something to fix, not to be toasted about), so `handledLocally`.
 * Every failure other than a plain validation error also means the tree we drew may be
 * stale -- a 404 (deleted), a 409 (renamed or filled meanwhile) -- so it is refetched.
 */
function useRefreshOnFailure() {
  const queryClient = useQueryClient();
  return (error: unknown, workspaceId: string) => {
    if (
      isApiError(error) &&
      (error.code === ErrorCode.NotFound ||
        error.code === ErrorCode.Conflict ||
        error.status === 409)
    ) {
      void queryClient.invalidateQueries({ queryKey: folderKeys.list(workspaceId) });
    }
  };
}

export function useCreateFolder(workspaceId: string) {
  const queryClient = useQueryClient();
  const refreshOnFailure = useRefreshOnFailure();

  return useMutation({
    mutationFn: ({ name, parentId }: { name: string; parentId: string | null }) =>
      folderApi.create(workspaceId, name, parentId),
    meta: { handledLocally: true },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: folderKeys.list(workspaceId) }),
    onError: (error) => refreshOnFailure(error, workspaceId),
  });
}

export function useRenameFolder(workspaceId: string) {
  const queryClient = useQueryClient();
  const refreshOnFailure = useRefreshOnFailure();

  return useMutation({
    mutationFn: ({
      folderId,
      name,
      expectedVersion,
    }: {
      folderId: string;
      name: string;
      expectedVersion: number;
    }) => folderApi.rename(workspaceId, folderId, name, expectedVersion),
    meta: { handledLocally: true },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: folderKeys.list(workspaceId) }),
    onError: (error) => refreshOnFailure(error, workspaceId),
  });
}

/**
 * Deleting a folder that is already gone is the outcome the user wanted, so a 404 is a
 * success. A 409 `FOLDER_NOT_EMPTY` is not: it is surfaced by the dialog, and the tree
 * is refetched so its counts match what the server just said.
 */
export function useDeleteFolder(workspaceId: string) {
  const queryClient = useQueryClient();
  const refreshOnFailure = useRefreshOnFailure();

  return useMutation({
    mutationFn: async (folderId: string) => {
      try {
        await folderApi.remove(workspaceId, folderId);
      } catch (error) {
        if (isApiError(error) && error.code === ErrorCode.NotFound) return;
        throw error;
      }
    },
    meta: { handledLocally: true },
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: folderKeys.list(workspaceId) }),
        queryClient.invalidateQueries({ queryKey: documentKeys.lists(workspaceId) }),
      ]),
    onError: (error) => refreshOnFailure(error, workspaceId),
  });
}
