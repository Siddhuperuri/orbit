import { api } from "@/lib/api/client";
import { uploadFile, type UploadProgress } from "@/lib/api/upload";
import type { DocumentFilters } from "@/features/documents/types";

export type ListDocumentsParams = DocumentFilters & {
  limit?: number;
  cursor?: string;
};

export interface UploadOptions {
  file: File;
  /** Defaults, server-side, to the filename. */
  title?: string;
  folderId?: string;
  onProgress?: (progress: UploadProgress) => void;
  signal?: AbortSignal;
}

/** A partial edit. `folderId: null` unfiles the document; leaving it `undefined` leaves the folder alone. */
export interface DocumentEdit {
  title?: string;
  folderId?: string | null;
}

/** Every document call. Components never build a URL or a query string. */
export const documentApi = {
  list: (workspaceId: string, params: ListDocumentsParams = {}, signal?: AbortSignal) =>
    api.get("/api/v1/workspaces/{workspace_id}/documents", {
      path: { workspace_id: workspaceId },
      query: {
        limit: params.limit,
        cursor: params.cursor,
        status: params.status,
        folder_id: params.folderId,
        unfiled: params.unfiled || undefined,
        tag_id: params.tagIds && params.tagIds.length > 0 ? [...params.tagIds] : undefined,
        q: params.q,
        sort: params.sort,
        archive: params.archive,
      },
      signal,
    }),

  get: (workspaceId: string, documentId: string, signal?: AbortSignal) =>
    api.get("/api/v1/workspaces/{workspace_id}/documents/{document_id}", {
      path: { workspace_id: workspaceId, document_id: documentId },
      signal,
    }),

  /**
   * Rename and/or move, under optimistic concurrency. `folder_id` is sent only when the
   * caller said something about it: the API reads its *presence* (absent leaves it,
   * `null` unfiles), so `undefined` must be omitted, not serialised as `null`.
   */
  update: (workspaceId: string, documentId: string, edit: DocumentEdit, expectedVersion: number) =>
    api.patch("/api/v1/workspaces/{workspace_id}/documents/{document_id}", {
      path: { workspace_id: workspaceId, document_id: documentId },
      json: {
        expected_version: expectedVersion,
        ...(edit.title !== undefined ? { title: edit.title } : {}),
        ...(edit.folderId !== undefined ? { folder_id: edit.folderId } : {}),
      },
    }),

  archive: (workspaceId: string, documentId: string) =>
    api.post("/api/v1/workspaces/{workspace_id}/documents/{document_id}/archive", {
      path: { workspace_id: workspaceId, document_id: documentId },
    }),

  restore: (workspaceId: string, documentId: string) =>
    api.post("/api/v1/workspaces/{workspace_id}/documents/{document_id}/restore", {
      path: { workspace_id: workspaceId, document_id: documentId },
    }),

  remove: (workspaceId: string, documentId: string) =>
    api.delete("/api/v1/workspaces/{workspace_id}/documents/{document_id}", {
      path: { workspace_id: workspaceId, document_id: documentId },
    }),

  processing: (workspaceId: string, documentId: string, signal?: AbortSignal) =>
    api.get("/api/v1/workspaces/{workspace_id}/documents/{document_id}/processing", {
      path: { workspace_id: workspaceId, document_id: documentId },
      signal,
    }),

  reprocess: (workspaceId: string, documentId: string) =>
    api.post("/api/v1/workspaces/{workspace_id}/documents/{document_id}/reprocess", {
      path: { workspace_id: workspaceId, document_id: documentId },
    }),

  /** A short-lived presigned URL; the API is not in the download data path. */
  downloadLink: (workspaceId: string, documentId: string) =>
    api.get("/api/v1/workspaces/{workspace_id}/documents/{document_id}/download", {
      path: { workspace_id: workspaceId, document_id: documentId },
    }),

  versions: (
    workspaceId: string,
    documentId: string,
    params: { before?: number; limit?: number } = {},
    signal?: AbortSignal,
  ) =>
    api.get("/api/v1/workspaces/{workspace_id}/documents/{document_id}/versions", {
      path: { workspace_id: workspaceId, document_id: documentId },
      query: { before: params.before, limit: params.limit },
      signal,
    }),

  versionDownloadLink: (workspaceId: string, documentId: string, versionId: string) =>
    api.get(
      "/api/v1/workspaces/{workspace_id}/documents/{document_id}/versions/{version_id}/download",
      {
        path: { workspace_id: workspaceId, document_id: documentId, version_id: versionId },
      },
    ),

  content: (
    workspaceId: string,
    documentId: string,
    params: { after?: number; limit?: number } = {},
    signal?: AbortSignal,
  ) =>
    api.get("/api/v1/workspaces/{workspace_id}/documents/{document_id}/content", {
      path: { workspace_id: workspaceId, document_id: documentId },
      query: { after: params.after, limit: params.limit },
      signal,
    }),

  addTag: (workspaceId: string, documentId: string, tagId: string) =>
    api.put("/api/v1/workspaces/{workspace_id}/documents/{document_id}/tags/{tag_id}", {
      path: { workspace_id: workspaceId, document_id: documentId, tag_id: tagId },
    }),

  removeTag: (workspaceId: string, documentId: string, tagId: string) =>
    api.delete("/api/v1/workspaces/{workspace_id}/documents/{document_id}/tags/{tag_id}", {
      path: { workspace_id: workspaceId, document_id: documentId, tag_id: tagId },
    }),

  upload: (workspaceId: string, { file, title, folderId, onProgress, signal }: UploadOptions) =>
    uploadFile("/api/v1/workspaces/{workspace_id}/documents", {
      path: { workspace_id: workspaceId },
      query: { title, folder_id: folderId },
      file,
      filename: file.name,
      onProgress,
      signal,
    }),

  addVersion: (
    workspaceId: string,
    documentId: string,
    { file, onProgress, signal }: Pick<UploadOptions, "file" | "onProgress" | "signal">,
  ) =>
    uploadFile("/api/v1/workspaces/{workspace_id}/documents/{document_id}/versions", {
      path: { workspace_id: workspaceId, document_id: documentId },
      file,
      filename: file.name,
      onProgress,
      signal,
    }),
};
