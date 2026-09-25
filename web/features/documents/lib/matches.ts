import type { Document, DocumentFilters } from "@/features/documents/types";

/**
 * Whether a document belongs in a list with these filters -- for the filters that can
 * be decided from the document alone.
 *
 * After a mutation, every cached list is corrected in place: an archived document
 * leaves the active lists, a document moved to another folder leaves the folder it was
 * in. That happens at once, so the row does not sit there contradicting what the user
 * just did while a refetch is in flight. It is deliberately *only* used to remove:
 * where a document now belongs is decided by the server (it also knows the sort order
 * and the page boundaries), so the refetch that follows is what adds it to the lists it
 * has joined.
 */
export function matchesFilters(document: Document, filters: DocumentFilters): boolean {
  const archived = document.archived_at !== null;
  if ((filters.archive === "archived") !== archived) return false;

  if (filters.folderId !== undefined && document.folder_id !== filters.folderId) return false;
  if (filters.unfiled && document.folder_id !== null) return false;

  if (filters.status !== undefined) {
    // A document with no version yet is queued: it is about to be.
    const status = document.current_version?.status ?? "pending";
    if (status !== filters.status) return false;
  }

  if (filters.tagIds && filters.tagIds.length > 0) {
    const have = new Set(document.tags.map((tag) => tag.id));
    if (!filters.tagIds.every((id) => have.has(id))) return false;
  }

  const q = filters.q?.trim().toLowerCase();
  if (q && !document.title.toLowerCase().includes(q)) return false;

  return true;
}
