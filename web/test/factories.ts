import type { Document, DocumentVersion, TagRef } from "@/features/documents/types";
import type { Folder } from "@/features/folders/types";
import type { Tag } from "@/features/tags/types";

/**
 * Typed builders for API objects, so a test states only what it cares about. Because
 * they return the *generated* types, a field added to the backend contract fails to
 * compile here once -- instead of in every test that spelled the object out.
 */

const NOW = "2026-09-19T12:00:00Z";
let counter = 0;
const nextId = (prefix: string) => `${prefix}-${(counter += 1)}`;

export function makeVersion(overrides: Partial<DocumentVersion> = {}): DocumentVersion {
  return {
    id: nextId("version"),
    version_number: 1,
    is_current: true,
    status: "ready",
    original_filename: "report.pdf",
    byte_size: 2048,
    content_type: "application/pdf",
    chunk_count: 12,
    page_count: 4,
    failure_code: null,
    failure_reason: null,
    processing_stage: null,
    processed_at: NOW,
    created_at: NOW,
    content_sha256: "ab".repeat(32),
    created_by_user_id: "user-1",
    ...overrides,
  };
}

export function makeDocument(overrides: Partial<Document> = {}): Document {
  return {
    id: nextId("doc"),
    title: "Quarterly Report",
    folder_id: null,
    version: 1,
    created_at: NOW,
    updated_at: NOW,
    created_by_user_id: "user-1",
    archived_at: null,
    tags: [],
    current_version: makeVersion(),
    ...overrides,
  };
}

/** A document whose current version is in the given state (and, if processing, stage). */
export function makeDocumentIn(
  status: DocumentVersion["status"],
  overrides: Partial<Document> = {},
  stage: DocumentVersion["processing_stage"] = null,
): Document {
  return makeDocument({
    ...overrides,
    current_version: makeVersion({
      status,
      processing_stage: stage,
      chunk_count: status === "ready" ? 12 : 0,
      processed_at: status === "ready" || status === "failed" ? NOW : null,
      failure_code: status === "failed" ? "DOCUMENT_CORRUPT" : null,
      failure_reason: status === "failed" ? "The file appears to be damaged." : null,
    }),
  });
}

export function makeFolder(overrides: Partial<Folder> = {}): Folder {
  return {
    id: nextId("folder"),
    name: "Reports",
    parent_id: null,
    depth: 0,
    version: 1,
    created_at: NOW,
    updated_at: NOW,
    document_count: 0,
    archived_document_count: 0,
    child_count: 0,
    ...overrides,
  };
}

export function makeTag(overrides: Partial<Tag> = {}): Tag {
  return {
    id: nextId("tag"),
    name: "Urgent",
    color: "danger",
    version: 1,
    created_at: NOW,
    updated_at: NOW,
    document_count: 0,
    ...overrides,
  };
}

export function makeTagRef(overrides: Partial<TagRef> = {}): TagRef {
  return { id: nextId("tag"), name: "Urgent", color: "danger", ...overrides };
}
