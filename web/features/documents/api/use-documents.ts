"use client";

import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { documentApi } from "@/features/documents/api/endpoints";
import {
  patchDocument,
  refreshOrganization,
  removeDocumentFromCaches,
} from "@/features/documents/api/cache";
import { documentKeys } from "@/features/documents/api/keys";
import type { Document, DocumentFilters } from "@/features/documents/types";
import { folderKeys } from "@/features/folders/api/keys";
import { tagKeys } from "@/features/tags/api/keys";
import { ErrorCode, isApiError } from "@/lib/api/errors";
import { startDownload } from "@/lib/utils/download";

/** Server-driven pagination throughout: the client never receives an unbounded collection. */
const PAGE_SIZE = 25;
const VERSION_PAGE_SIZE = 20;
const PASSAGE_PAGE_SIZE = 40;

export function useDocuments(workspaceId: string, filters: DocumentFilters = {}) {
  return useInfiniteQuery({
    queryKey: documentKeys.list(workspaceId, filters),
    queryFn: ({ pageParam, signal }) =>
      documentApi.list(workspaceId, { ...filters, limit: PAGE_SIZE, cursor: pageParam }, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    // Typing in the title filter changes the key on every pause; without this each
    // change would blank the list to a skeleton and back. The previous results stay on
    // screen (marked stale via `isPlaceholderData`) until the new ones arrive.
    placeholderData: keepPreviousData,
  });
}

export function useDocument(workspaceId: string, documentId: string) {
  return useQuery({
    queryKey: documentKeys.detail(workspaceId, documentId),
    queryFn: ({ signal }) => documentApi.get(workspaceId, documentId, signal),
    // Coming back to this tab is the moment another person's change is most likely to
    // have happened and least likely to be known. The global default is off, on purpose,
    // for data that rarely moves; a document someone may be editing is the exception.
    refetchOnWindowFocus: true,
  });
}

/** The pipeline's report: every attempt made, for the history beneath the headline state. */
export function useProcessingReport(workspaceId: string, documentId: string, enabled = true) {
  return useQuery({
    queryKey: documentKeys.processing(workspaceId, documentId),
    queryFn: ({ signal }) => documentApi.processing(workspaceId, documentId, signal),
    enabled,
  });
}

/**
 * A document's revisions, newest first, older ones on demand. The API pages by version
 * number (`before`), which new uploads only ever extend at the top, so reading back
 * through history is never disturbed by one arriving.
 */
export function useDocumentVersions(
  workspaceId: string,
  documentId: string,
  currentVersionId?: string,
) {
  return useInfiniteQuery({
    queryKey: documentKeys.versions(workspaceId, documentId, currentVersionId),
    queryFn: ({ pageParam, signal }) =>
      documentApi.versions(
        workspaceId,
        documentId,
        { before: pageParam, limit: VERSION_PAGE_SIZE },
        signal,
      ),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (lastPage) => lastPage.next_before ?? undefined,
  });
}

/**
 * The indexed text of the current version, in passages, a page at a time. Keyed by
 * version number: a new upload is a different text, and must not be shown as the old.
 */
export function useDocumentContent(
  workspaceId: string,
  documentId: string,
  versionNumber: number,
  enabled: boolean,
) {
  return useInfiniteQuery({
    queryKey: documentKeys.content(workspaceId, documentId, versionNumber),
    queryFn: ({ pageParam, signal }) =>
      documentApi.content(
        workspaceId,
        documentId,
        { after: pageParam, limit: PASSAGE_PAGE_SIZE },
        signal,
      ),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (lastPage) => lastPage.next_after ?? undefined,
    enabled,
  });
}

/** What every failed edit has in common: the copy we hold is stale, so get the current one. */
function refreshAfterFailedEdit(
  queryClient: ReturnType<typeof useQueryClient>,
  workspaceId: string,
  documentId: string,
) {
  void queryClient.invalidateQueries({ queryKey: documentKeys.detail(workspaceId, documentId) });
  // A 404 on a *folder* means the tree we drew is out of date too.
  void queryClient.invalidateQueries({ queryKey: folderKeys.list(workspaceId) });
}

/**
 * Rename is optimistic: the outcome is predictable, so the new title appears at once
 * and is rolled back if the server refuses (a 409 because someone else edited the
 * document first, or a validation failure). Failure is reported inline by the caller,
 * so there is no toast on top of it.
 */
export function useRenameDocument(workspaceId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ document, title }: { document: Document; title: string }) =>
      documentApi.update(workspaceId, document.id, { title }, document.version),
    meta: { handledLocally: true },

    onMutate: async ({ document, title }) => {
      await queryClient.cancelQueries({ queryKey: documentKeys.detail(workspaceId, document.id) });
      patchDocument(queryClient, workspaceId, { ...document, title });
      return { previous: document };
    },
    onError: (_error, { document }, context) => {
      if (context) patchDocument(queryClient, workspaceId, context.previous);
      refreshAfterFailedEdit(queryClient, workspaceId, document.id);
    },
    onSuccess: (document) => {
      patchDocument(queryClient, workspaceId, document);
      // Order may depend on the title or on `updated_at`.
      void queryClient.invalidateQueries({ queryKey: documentKeys.lists(workspaceId) });
    },
  });
}

/**
 * Filing is *not* optimistic: whether the target folder still exists is the server's
 * to say, and a document that jumps into a folder it then bounces out of is worse than
 * a short wait. `folderId: null` takes it out of its folder.
 */
export function useMoveDocument(workspaceId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ document, folderId }: { document: Document; folderId: string | null }) =>
      documentApi.update(workspaceId, document.id, { folderId }, document.version),
    meta: { handledLocally: true },
    onSuccess: (document) => {
      patchDocument(queryClient, workspaceId, document);
      void refreshOrganization(queryClient, workspaceId);
    },
    onError: (_error, { document }) =>
      refreshAfterFailedEdit(queryClient, workspaceId, document.id),
  });
}

/**
 * Archive and restore are idempotent on the server -- asking for the state a document
 * is already in succeeds -- so two people doing it at once both get the outcome they
 * asked for, and a stale page cannot produce an error here.
 */
export function useArchiveDocument(workspaceId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ documentId, archived }: { documentId: string; archived: boolean }) =>
      archived
        ? documentApi.archive(workspaceId, documentId)
        : documentApi.restore(workspaceId, documentId),
    meta: { errorTitle: "Couldn't change the archive state" },
    onSuccess: (document) => {
      // Leaves (or joins) the lists at once, then the refetch settles order and paging.
      patchDocument(queryClient, workspaceId, document);
      void refreshOrganization(queryClient, workspaceId);
    },
  });
}

/**
 * Delete is pessimistic: the user is told it is gone only once the server agrees. And
 * it is idempotent from the user's side -- if someone else already deleted it, the
 * document is gone, which is what was asked for, so a 404 is a success rather than an
 * error to explain.
 */
export function useDeleteDocument(workspaceId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (documentId: string) => {
      try {
        await documentApi.remove(workspaceId, documentId);
      } catch (error) {
        if (isApiError(error) && error.code === ErrorCode.NotFound) return;
        throw error;
      }
    },
    meta: { handledLocally: true },
    onSuccess: (_result, documentId) => {
      removeDocumentFromCaches(queryClient, workspaceId, documentId);
      return refreshOrganization(queryClient, workspaceId);
    },
  });
}

export function useReprocessDocument(workspaceId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (documentId: string) => documentApi.reprocess(workspaceId, documentId),
    meta: { errorTitle: "Couldn't restart processing" },
    onSuccess: (document) => {
      patchDocument(queryClient, workspaceId, document);
      // The attempts list changed too.
      return queryClient.invalidateQueries({
        queryKey: documentKeys.processing(workspaceId, document.id),
      });
    },
  });
}

/**
 * Downloads never proxy through the API: it hands back a short-lived presigned URL
 * and the browser fetches straight from storage. The link is single-use in spirit, so
 * it is requested at click time rather than cached.
 */
export function useDownloadDocument(workspaceId: string) {
  return useMutation({
    mutationFn: (documentId: string) => documentApi.downloadLink(workspaceId, documentId),
    meta: { errorTitle: "Couldn't start the download" },
    onSuccess: ({ url }) => startDownload(url),
  });
}

/** As {@link useDownloadDocument}, for any revision: earlier versions keep their file. */
export function useDownloadVersion(workspaceId: string) {
  return useMutation({
    mutationFn: ({ documentId, versionId }: { documentId: string; versionId: string }) =>
      documentApi.versionDownloadLink(workspaceId, documentId, versionId),
    meta: { errorTitle: "Couldn't start the download" },
    onSuccess: ({ url }) => startDownload(url),
  });
}

/**
 * Attach or detach one tag. Both are idempotent set operations on the server, so two
 * people tagging the same document never overwrite each other -- and the response is
 * the whole document, so the tags shown are what the server has, not what we assumed.
 */
export function useToggleDocumentTag(workspaceId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      documentId,
      tagId,
      attached,
    }: {
      documentId: string;
      tagId: string;
      attached: boolean;
    }) =>
      attached
        ? documentApi.addTag(workspaceId, documentId, tagId)
        : documentApi.removeTag(workspaceId, documentId, tagId),
    meta: { errorTitle: "Couldn't change the tags" },
    onSuccess: (document) => {
      patchDocument(queryClient, workspaceId, document);
      // Usage counts on the tags list; and a list filtered by tag may have changed.
      void queryClient.invalidateQueries({ queryKey: tagKeys.list(workspaceId) });
      void queryClient.invalidateQueries({ queryKey: documentKeys.lists(workspaceId) });
    },
    onError: (error, { documentId }) => {
      // "That tag no longer exists": the list we offered it from is stale.
      if (isApiError(error) && error.code === ErrorCode.NotFound) {
        void queryClient.invalidateQueries({ queryKey: tagKeys.list(workspaceId) });
        void queryClient.invalidateQueries({
          queryKey: documentKeys.detail(workspaceId, documentId),
        });
      }
    },
  });
}
