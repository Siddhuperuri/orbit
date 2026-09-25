import type { InfiniteData, QueryClient } from "@tanstack/react-query";

import { documentKeys } from "@/features/documents/api/keys";
import { matchesFilters } from "@/features/documents/lib/matches";
import type { Document, DocumentFilters } from "@/features/documents/types";
import { folderKeys } from "@/features/folders/api/keys";
import { tagKeys } from "@/features/tags/api/keys";
import type { Schema } from "@/lib/api/types";

/**
 * Keeps every cached copy of a document in agreement.
 *
 * The same document lives in the detail cache and in every list that contains it.
 * Patching them together (rather than refetching) is what makes a rename or a
 * status change appear at once in all three places -- the reason for having one
 * cache instead of one per view (ADR-0016).
 */

type DocumentPage = Schema<"PageResponse_DocumentResponse_">;

function mapPages(
  data: InfiniteData<DocumentPage> | undefined,
  transform: (items: readonly Document[]) => Document[],
): InfiniteData<DocumentPage> | undefined {
  if (!data) return data;
  return { ...data, pages: data.pages.map((page) => ({ ...page, items: transform(page.items) })) };
}

/** The filters a cached list was fetched with: the last element of its key. */
function filtersOf(queryKey: readonly unknown[]): DocumentFilters {
  const last = queryKey[queryKey.length - 1];
  return typeof last === "object" && last !== null ? (last as DocumentFilters) : {};
}

/**
 * Brings every *list* containing the document in line: the copy is replaced, and where
 * the change means it no longer belongs (archived, moved to another folder, no longer
 * carrying a filtered tag) it is dropped instead. Separate from {@link patchDocument}
 * because the processing watcher owns the detail entry it polls: writing it from
 * inside its own effect would bump `dataUpdatedAt`, which re-triggers the effect,
 * forever.
 */
export function patchDocumentInLists(
  queryClient: QueryClient,
  workspaceId: string,
  document: Document,
) {
  const lists = queryClient.getQueriesData<InfiniteData<DocumentPage>>({
    queryKey: documentKeys.lists(workspaceId),
  });
  for (const [queryKey, data] of lists) {
    const filters = filtersOf(queryKey);
    const belongs = matchesFilters(document, filters);
    queryClient.setQueryData<InfiniteData<DocumentPage>>(queryKey, () =>
      mapPages(data, (items) =>
        belongs
          ? items.map((item) => (item.id === document.id ? document : item))
          : items.filter((item) => item.id !== document.id),
      ),
    );
  }
}

export function patchDocument(queryClient: QueryClient, workspaceId: string, document: Document) {
  queryClient.setQueryData(documentKeys.detail(workspaceId, document.id), document);
  patchDocumentInLists(queryClient, workspaceId, document);
}

export function removeDocumentFromCaches(
  queryClient: QueryClient,
  workspaceId: string,
  documentId: string,
) {
  queryClient.removeQueries({ queryKey: documentKeys.detail(workspaceId, documentId) });
  queryClient.setQueriesData<InfiniteData<DocumentPage>>(
    { queryKey: documentKeys.lists(workspaceId) },
    (data) => mapPages(data, (items) => items.filter((item) => item.id !== documentId)),
  );
}

/**
 * Everything whose *counts or contents* depend on which documents exist and where they
 * are: the lists (order and paging), the folder tree's counts, and the tags' usage
 * counts. Called after anything that adds, moves, archives, or deletes a document.
 */
export function refreshOrganization(queryClient: QueryClient, workspaceId: string) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: documentKeys.lists(workspaceId) }),
    queryClient.invalidateQueries({ queryKey: folderKeys.list(workspaceId) }),
    queryClient.invalidateQueries({ queryKey: tagKeys.list(workspaceId) }),
  ]);
}
