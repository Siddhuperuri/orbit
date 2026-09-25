import { workspaceKeys } from "@/features/workspaces/api/keys";
import type { DocumentFilters } from "@/features/documents/types";

/**
 * Hierarchical, so an upload invalidates `lists(ws)` and touches nothing else,
 * while a rename patches `detail(ws, id)` and every list that might contain it.
 *
 * Everything about one document -- its processing report, versions, and text -- hangs
 * off `detail(ws, id)`, so removing or invalidating a document takes all of it along.
 */
export const documentKeys = {
  all: (workspaceId: string) => [...workspaceKeys.scope(workspaceId), "documents"] as const,
  lists: (workspaceId: string) => [...documentKeys.all(workspaceId), "list"] as const,
  list: (workspaceId: string, filters: DocumentFilters) =>
    [...documentKeys.lists(workspaceId), filters] as const,
  details: (workspaceId: string) => [...documentKeys.all(workspaceId), "detail"] as const,
  detail: (workspaceId: string, documentId: string) =>
    [...documentKeys.details(workspaceId), documentId] as const,
  processing: (workspaceId: string, documentId: string) =>
    [...documentKeys.detail(workspaceId, documentId), "processing"] as const,
  /**
   * Keyed by the *current* version's id when known, so the history refetches when anyone --
   * this user, or someone else whose upload this page learns about on its next refresh -- adds a
   * version. Invalidating with the id omitted still matches every one of them.
   */
  versions: (workspaceId: string, documentId: string, currentVersionId?: string) =>
    [
      ...documentKeys.detail(workspaceId, documentId),
      "versions",
      ...(currentVersionId ? [currentVersionId] : []),
    ] as const,
  /** Keyed by version number, so uploading a new version starts a fresh read rather than showing the old text. */
  content: (workspaceId: string, documentId: string, versionNumber: number) =>
    [...documentKeys.detail(workspaceId, documentId), "content", versionNumber] as const,
};
