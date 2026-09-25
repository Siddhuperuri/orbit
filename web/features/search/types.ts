import type { Schema } from "@/lib/api/types";

export type SearchResponse = Schema<"SearchResponseOut">;
export type SearchResult = Schema<"SearchResultOut">;
export type SearchMode = Schema<"RetrievalMode">;

export const SEARCH_MODES: readonly SearchMode[] = ["hybrid", "lexical", "semantic"];

export const MODE_LABELS: Record<SearchMode, string> = {
  hybrid: "Hybrid",
  lexical: "Keyword",
  semantic: "Meaning",
};

export const MODE_DESCRIPTIONS: Record<SearchMode, string> = {
  hybrid: "Combines keyword and meaning matches. Best for most searches.",
  lexical: "Matches the words you type. Best for names, codes, and exact phrases.",
  semantic: "Matches by meaning, even when the words differ.",
};

/**
 * The document types ORBIT stores, as a closed set.
 *
 * Filtering by type is a checklist rather than free text because the accepted set
 * is exactly these three (the upload validator's map). A type that ORBIT cannot
 * store cannot be in the index, so offering it as a filter would be offering an
 * always-empty result.
 */
export const CONTENT_TYPES = ["application/pdf", "text/markdown", "text/plain"] as const;
export type ContentTypeFilter = (typeof CONTENT_TYPES)[number];

const CONTENT_TYPE_LABELS: Record<string, string> = {
  "application/pdf": "PDF",
  "text/markdown": "Markdown",
  "text/plain": "Text",
};

/** "PDF", "Markdown", "Text" -- what a person calls the file, not its media type. */
export function contentTypeLabel(contentType: string): string {
  return CONTENT_TYPE_LABELS[contentType] ?? "Document";
}

/** The limits the API enforces, mirrored so the form can state and respect them. */
export const MAX_QUERY_CHARACTERS = 2000;
export const DEFAULT_RESULT_LIMIT = 20;
export const MAX_RESULT_LIMIT = 50;
export const MAX_TAG_FILTERS = 5;
/** The API refuses a deeper page: fusion ranks a pool, so depth costs retrieval. */
export const MAX_SEARCH_OFFSET = 200;
