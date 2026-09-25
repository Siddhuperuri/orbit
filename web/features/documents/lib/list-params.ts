import type {
  ArchiveFilter,
  DocumentFilters,
  DocumentSort,
  ProcessingStatus,
} from "@/features/documents/types";
import { routes } from "@/lib/navigation";

/**
 * The document list's state, and its round trip through the URL.
 *
 * Every filter, the sort, and the search text live in the address, so a view can be
 * bookmarked, shared, and survives a reload or the back button -- and the page itself
 * holds no copy that could disagree with it. What is parsed here is *untrusted*
 * (anyone can edit a URL), so anything that is not a recognised value falls back to
 * the default instead of reaching the API as a 422.
 */

export const MAX_TAG_FILTERS = 5;
/** Matches the API's limit on the title filter. */
export const MAX_SEARCH_LENGTH = 100;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const SORT_OPTIONS: ReadonlyArray<{ value: DocumentSort; label: string }> = [
  { value: "created_desc", label: "Newest first" },
  { value: "created_asc", label: "Oldest first" },
  { value: "updated_desc", label: "Recently updated" },
  { value: "title_asc", label: "Title A–Z" },
  { value: "title_desc", label: "Title Z–A" },
];

const SORTS = SORT_OPTIONS.map((option) => option.value);
const STATUSES: readonly ProcessingStatus[] = ["pending", "processing", "ready", "failed"];

export type FolderScope = { kind: "all" } | { kind: "unfiled" } | { kind: "folder"; id: string };

export interface DocumentListState {
  q: string;
  sort: DocumentSort;
  status: ProcessingStatus | undefined;
  folder: FolderScope;
  tags: readonly string[];
  archive: ArchiveFilter;
}

export const DEFAULT_LIST_STATE: DocumentListState = {
  q: "",
  sort: "created_desc",
  status: undefined,
  folder: { kind: "all" },
  tags: [],
  archive: "active",
};

interface ParamSource {
  get(name: string): string | null;
  getAll(name: string): string[];
}

export function parseListState(params: ParamSource): DocumentListState {
  const sort = SORTS.find((value) => value === params.get("sort")) ?? DEFAULT_LIST_STATE.sort;
  const status = STATUSES.find((value) => value === params.get("status"));

  const folderParam = params.get("folder");
  const folder: FolderScope =
    folderParam === "none"
      ? { kind: "unfiled" }
      : folderParam && UUID.test(folderParam)
        ? { kind: "folder", id: folderParam }
        : { kind: "all" };

  const tags = [...new Set(params.getAll("tag").filter((id) => UUID.test(id)))].slice(
    0,
    MAX_TAG_FILTERS,
  );

  return {
    q: (params.get("q") ?? "").slice(0, MAX_SEARCH_LENGTH),
    sort,
    status,
    folder,
    tags,
    archive: params.get("archive") === "archived" ? "archived" : "active",
  };
}

/** The state as a query string, omitting every default so an untouched list has a clean URL. */
export function listStateToSearch(state: DocumentListState): URLSearchParams {
  const search = new URLSearchParams();
  const q = state.q.trim();
  if (q) search.set("q", q);
  if (state.sort !== DEFAULT_LIST_STATE.sort) search.set("sort", state.sort);
  if (state.status) search.set("status", state.status);
  if (state.folder.kind === "unfiled") search.set("folder", "none");
  if (state.folder.kind === "folder") search.set("folder", state.folder.id);
  for (const tag of state.tags) search.append("tag", tag);
  if (state.archive === "archived") search.set("archive", "archived");
  return search;
}

export function documentsHref(workspaceId: string, state: DocumentListState): string {
  const search = listStateToSearch(state).toString();
  return `${routes.documents(workspaceId)}${search ? `?${search}` : ""}`;
}

/** The state in the shape the API takes. */
export function listStateToFilters(state: DocumentListState): DocumentFilters {
  const filters: DocumentFilters = {};
  const q = state.q.trim();
  if (q) filters.q = q;
  if (state.sort !== DEFAULT_LIST_STATE.sort) filters.sort = state.sort;
  if (state.status) filters.status = state.status;
  if (state.folder.kind === "unfiled") filters.unfiled = true;
  if (state.folder.kind === "folder") filters.folderId = state.folder.id;
  if (state.tags.length > 0) filters.tagIds = state.tags;
  if (state.archive === "archived") filters.archive = "archived";
  return filters;
}

/**
 * Whether anything is narrowing the list -- what decides between "this workspace has
 * no documents" and "nothing matches". Sort does not narrow.
 */
export function isNarrowed(state: DocumentListState): boolean {
  return (
    state.q.trim() !== "" ||
    state.status !== undefined ||
    state.folder.kind !== "all" ||
    state.tags.length > 0 ||
    state.archive !== "active"
  );
}

/** The state with the filters cleared but the sort, and the archive/folder scope, kept. */
export function withoutFilters(state: DocumentListState): DocumentListState {
  return { ...state, q: "", status: undefined, tags: [] };
}

export function toggleTag(state: DocumentListState, tagId: string): DocumentListState {
  if (state.tags.includes(tagId)) {
    return { ...state, tags: state.tags.filter((id) => id !== tagId) };
  }
  if (state.tags.length >= MAX_TAG_FILTERS) return state;
  return { ...state, tags: [...state.tags, tagId] };
}

/**
 * A link to a folder scope (or the archive), from wherever the user is now. Navigating
 * to a different place starts a fresh view -- the search text and filters from the last
 * one would silently hide things -- but the sort is a preference, so it stays.
 */
export function scopeHref(
  workspaceId: string,
  current: DocumentListState,
  scope: FolderScope,
  archive: ArchiveFilter = "active",
): string {
  return documentsHref(workspaceId, {
    ...DEFAULT_LIST_STATE,
    sort: current.sort,
    folder: scope,
    archive,
  });
}
