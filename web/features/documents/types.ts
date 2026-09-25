import type { Schema } from "@/lib/api/types";

export type Document = Schema<"DocumentResponse">;
export type DocumentVersion = Schema<"DocumentVersionResponse">;
export type ProcessingStatus = Schema<"ProcessingStatus">;
export type ProcessingReport = Schema<"ProcessingStatusResponse">;
export type ProcessingAttempt = Schema<"ProcessingAttemptResponse">;
export type PipelineStage = Schema<"PipelineStage">;
export type DocumentSort = Schema<"DocumentSort">;
export type ArchiveFilter = Schema<"ArchiveFilter">;
export type Passage = Schema<"PassageResponse">;
export type DocumentContent = Schema<"DocumentContentResponse">;
export type VersionList = Schema<"DocumentVersionListResponse">;
export type TagRef = Schema<"TagRefResponse">;

/**
 * Server-side list filters, in the shape the API takes. Anything filterable is a
 * query parameter, never client-side over a page -- a filter over the loaded page
 * would silently be wrong the moment there is a second page.
 */
export interface DocumentFilters {
  status?: ProcessingStatus;
  /** A case-insensitive substring of the *title*. Not content search. */
  q?: string;
  sort?: DocumentSort;
  folderId?: string;
  /** Only documents in no folder. Not combinable with `folderId`. */
  unfiled?: boolean;
  /** A document must carry every one of these. */
  tagIds?: readonly string[];
  archive?: ArchiveFilter;
}
