import { workspaceKeys } from "@/features/workspaces/api/keys";
import type { SearchState } from "@/features/search/lib/search-params";

export const searchKeys = {
  all: (workspaceId: string) => [...workspaceKeys.scope(workspaceId), "search"] as const,
  /**
   * Keyed on the whole state, so every distinct search -- a different page, a
   * lifted filter -- is its own cache entry rather than one entry that thrashes.
   */
  results: (workspaceId: string, state: SearchState) =>
    [...searchKeys.all(workspaceId), state] as const,
};
