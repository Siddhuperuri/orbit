"use client";

import { useQuery } from "@tanstack/react-query";

import { documentApi } from "@/features/documents/api/endpoints";
import { documentKeys } from "@/features/documents/api/keys";

/** Enough to jump to a document by name without paginating; the palette is a shortcut, not a browser. */
const PALETTE_LIMIT = 50;

/**
 * Recent documents for the command palette. Fetched only while the palette is
 * open and a workspace is selected, and keyed under the documents lists so an
 * upload or delete invalidates it along with everything else.
 */
export function usePaletteDocuments(workspaceId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: [...documentKeys.lists(workspaceId ?? ""), "palette"],
    queryFn: ({ signal }) => documentApi.list(workspaceId ?? "", { limit: PALETTE_LIMIT }, signal),
    enabled: enabled && Boolean(workspaceId),
    select: (page) => page.items,
  });
}
