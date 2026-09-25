import {
  DEFAULT_RESULT_LIMIT,
  MAX_QUERY_CHARACTERS,
  MAX_SEARCH_OFFSET,
  MAX_TAG_FILTERS,
  SEARCH_MODES,
  type ContentTypeFilter,
  type SearchMode,
} from "@/features/search/types";
import { routes } from "@/lib/navigation";

/**
 * A search's state, and its round trip through the URL.
 *
 * The query, the mode, every filter, and the page all live in the address, so a
 * search can be shared and survives a reload or the back button -- and the page
 * holds no second copy that could disagree with it.
 *
 * What is parsed here is *untrusted*: anyone can edit a URL. Anything that is not
 * a recognised value falls back to the default rather than reaching the API as a
 * 422, and the limits the API enforces are applied here too, so a hand-edited
 * `&page=9999` becomes the last page it will serve instead of an error.
 */

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Folder scope, in the three states the API distinguishes. */
export type FolderScope = { kind: "all" } | { kind: "unfiled" } | { kind: "folder"; id: string };

export interface SearchState {
  query: string;
  mode: SearchMode;
  folder: FolderScope;
  /** A document must carry every one of these. */
  tags: readonly string[];
  types: readonly ContentTypeFilter[];
  /** Restrict to one document -- "search within", from a document's page. */
  documentId: string | undefined;
  /** 0-based. */
  page: number;
}

export const DEFAULT_SEARCH_STATE: SearchState = {
  query: "",
  mode: "hybrid",
  folder: { kind: "all" },
  tags: [],
  types: [],
  documentId: undefined,
  page: 0,
};

/** The last page the API will serve, given its offset ceiling. */
export const MAX_PAGE = Math.floor(MAX_SEARCH_OFFSET / DEFAULT_RESULT_LIMIT);

const CONTENT_TYPES: readonly ContentTypeFilter[] = [
  "application/pdf",
  "text/markdown",
  "text/plain",
];

interface ParamSource {
  get(name: string): string | null;
  getAll(name: string): string[];
}

export function parseSearchState(params: ParamSource): SearchState {
  const folderParam = params.get("folder");
  const folder: FolderScope =
    folderParam === "none"
      ? { kind: "unfiled" }
      : folderParam && UUID.test(folderParam)
        ? { kind: "folder", id: folderParam }
        : { kind: "all" };

  const documentId = params.get("doc");
  const page = Number.parseInt(params.get("page") ?? "", 10);

  return {
    query: (params.get("q") ?? "").trim().slice(0, MAX_QUERY_CHARACTERS),
    mode: SEARCH_MODES.find((mode) => mode === params.get("mode")) ?? DEFAULT_SEARCH_STATE.mode,
    folder,
    tags: [...new Set(params.getAll("tag").filter((id) => UUID.test(id)))].slice(
      0,
      MAX_TAG_FILTERS,
    ),
    types: [...new Set(params.getAll("type"))].filter((value): value is ContentTypeFilter =>
      CONTENT_TYPES.includes(value as ContentTypeFilter),
    ),
    documentId: documentId && UUID.test(documentId) ? documentId : undefined,
    page: Number.isInteger(page) && page > 0 ? Math.min(page, MAX_PAGE) : 0,
  };
}

/** The state as a query string, omitting every default so a plain search has a clean URL. */
export function searchStateToQuery(state: SearchState): URLSearchParams {
  const params = new URLSearchParams();
  const query = state.query.trim();
  if (query) params.set("q", query);
  if (state.mode !== DEFAULT_SEARCH_STATE.mode) params.set("mode", state.mode);
  if (state.folder.kind === "unfiled") params.set("folder", "none");
  if (state.folder.kind === "folder") params.set("folder", state.folder.id);
  for (const tag of state.tags) params.append("tag", tag);
  for (const type of state.types) params.append("type", type);
  if (state.documentId) params.set("doc", state.documentId);
  if (state.page > 0) params.set("page", String(state.page));
  return params;
}

export function searchHref(workspaceId: string, state: SearchState): string {
  const query = searchStateToQuery(state).toString();
  return `${routes.search(workspaceId)}${query ? `?${query}` : ""}`;
}

/**
 * Whether anything beyond the workspace is narrowing this search.
 *
 * What decides between "your documents don't mention this" and "nothing matches
 * *with these filters*" -- which are different things to tell someone, and only
 * one of them has an obvious next step.
 */
export function isNarrowed(state: SearchState): boolean {
  return (
    state.folder.kind !== "all" ||
    state.tags.length > 0 ||
    state.types.length > 0 ||
    state.documentId !== undefined
  );
}

/** The same query with every filter lifted, back on the first page. */
export function withoutFilters(state: SearchState): SearchState {
  return {
    ...state,
    folder: { kind: "all" },
    tags: [],
    types: [],
    documentId: undefined,
    page: 0,
  };
}

/**
 * Changing what is searched returns to page one.
 *
 * Staying on page 4 while the filters change would show results 61-80 of a
 * ranking the user has not seen the top of -- and often an empty page, which
 * reads as "no matches" when the truth is "no matches *this far down*".
 */
export function refine(state: SearchState, changes: Partial<SearchState>): SearchState {
  return { ...state, ...changes, page: 0 };
}

export function toggleTag(state: SearchState, tagId: string): SearchState {
  if (state.tags.includes(tagId)) {
    return refine(state, { tags: state.tags.filter((id) => id !== tagId) });
  }
  if (state.tags.length >= MAX_TAG_FILTERS) return state;
  return refine(state, { tags: [...state.tags, tagId] });
}

export function toggleType(state: SearchState, type: ContentTypeFilter): SearchState {
  return refine(state, {
    types: state.types.includes(type)
      ? state.types.filter((value) => value !== type)
      : [...state.types, type],
  });
}
