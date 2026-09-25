"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { searchKeys } from "@/features/search/api/keys";
import type { SearchState } from "@/features/search/lib/search-params";
import { DEFAULT_RESULT_LIMIT } from "@/features/search/types";
import { api } from "@/lib/api/client";

/**
 * Search is a `POST` (the query is user content that must stay out of access logs
 * and history) but semantically a read, so it is a *query*: cached, deduplicated,
 * and cancelled if the user searches again before it returns.
 *
 * It runs only for a non-empty query, and previous results stay on screen while
 * the next set loads -- swapping the whole list for a skeleton on every refinement
 * makes a fast search feel slow, and makes paging flicker.
 */
export function useSearch(workspaceId: string, state: SearchState) {
  const query = state.query.trim();
  const normalized: SearchState = { ...state, query };

  return useQuery({
    queryKey: searchKeys.results(workspaceId, normalized),
    queryFn: ({ signal }) =>
      api.post("/api/v1/workspaces/{workspace_id}/search", {
        path: { workspace_id: workspaceId },
        json: {
          query,
          mode: state.mode,
          limit: DEFAULT_RESULT_LIMIT,
          offset: state.page * DEFAULT_RESULT_LIMIT,
          // Every filter is sent as `null` when unset rather than omitted, so the
          // request says "no filter" explicitly and an empty array can never be
          // sent -- which the API rejects, since it would mean "match nothing".
          document_ids: state.documentId ? [state.documentId] : null,
          folder_id: state.folder.kind === "folder" ? state.folder.id : null,
          unfiled: state.folder.kind === "unfiled",
          tag_ids: state.tags.length > 0 ? [...state.tags] : null,
          content_types: state.types.length > 0 ? [...state.types] : null,
        },
        signal,
      }),
    enabled: query.length > 0,
    placeholderData: keepPreviousData,
    // A repeated identical search within a minute is the user going back, not
    // asking again; it should not cost another embedding call.
    staleTime: 60_000,
  });
}
