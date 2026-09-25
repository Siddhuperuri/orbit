"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { patchDocument, refreshOrganization } from "@/features/documents/api/cache";
import { documentApi } from "@/features/documents/api/endpoints";
import { documentKeys } from "@/features/documents/api/keys";
import type { Document } from "@/features/documents/types";
import type { UploadPhase } from "@/features/documents/upload/upload-queue";
import { useUploadPolicy } from "@/features/documents/upload/use-upload-policy";
import { validateFile, type Rejection } from "@/features/documents/upload/validate";
import { isAbortError } from "@/lib/api/errors";

/**
 * Where a "new version" upload is. The same honest phases as the upload queue: bytes going
 * out (with a fraction when the browser has one, `null` when it does not), then "saving"
 * once every byte is sent but the server has not answered.
 */
export type VersionUploadState =
  | { phase: "idle" }
  | { phase: "uploading"; fileName: string; progress: number | null; step: UploadPhase }
  | { phase: "rejected"; fileName: string; rejection: Rejection }
  | { phase: "failed"; fileName: string; error: unknown }
  /** Identical content already exists as another document: nothing was added here. */
  | { phase: "duplicate"; fileName: string; existing: Document }
  | { phase: "done"; fileName: string; versionNumber: number | null };

/**
 * Replace a document's current version with a new file, showing real progress.
 *
 * One upload at a time per document: the API serialises concurrent uploads to the same
 * document under a lock, so a second in flight would only wait, and two progress bars for
 * one document would be confusing. A rejected or failed file leaves the document exactly
 * as it was -- the server only switches the current version once the new one is stored.
 */
export function useVersionUpload(workspaceId: string, documentId: string) {
  const queryClient = useQueryClient();
  const policy = useUploadPolicy();
  const [state, setState] = useState<VersionUploadState>({ phase: "idle" });
  const controller = useRef<AbortController | null>(null);

  // Leaving the page abandons the transfer rather than leaving it running unseen.
  useEffect(() => () => controller.current?.abort(), []);

  const start = useCallback(
    async (file: File) => {
      const rejection = validateFile(file, policy);
      if (rejection) {
        setState({ phase: "rejected", fileName: file.name, rejection });
        return;
      }

      const abort = new AbortController();
      controller.current = abort;
      setState({ phase: "uploading", fileName: file.name, progress: 0, step: "sending" });

      let saving = false;
      try {
        const result = await documentApi.addVersion(workspaceId, documentId, {
          file,
          signal: abort.signal,
          onProgress: ({ fraction }) => {
            if (saving) return;
            if (fraction === null) {
              setState({
                phase: "uploading",
                fileName: file.name,
                progress: null,
                step: "sending",
              });
            } else if (fraction >= 1) {
              saving = true;
              setState({ phase: "uploading", fileName: file.name, progress: 1, step: "saving" });
            } else {
              setState({
                phase: "uploading",
                fileName: file.name,
                progress: fraction,
                step: "sending",
              });
            }
          },
        });

        if (result.deduplicated && result.document.id !== documentId) {
          setState({ phase: "duplicate", fileName: file.name, existing: result.document });
          return;
        }

        patchDocument(queryClient, workspaceId, result.document);
        void queryClient.invalidateQueries({
          queryKey: documentKeys.versions(workspaceId, documentId),
        });
        void queryClient.invalidateQueries({
          queryKey: documentKeys.processing(workspaceId, documentId),
        });
        void refreshOrganization(queryClient, workspaceId);
        setState({
          phase: "done",
          fileName: file.name,
          versionNumber: result.document.current_version?.version_number ?? null,
        });
      } catch (error) {
        setState(
          isAbortError(error) ? { phase: "idle" } : { phase: "failed", fileName: file.name, error },
        );
      } finally {
        controller.current = null;
      }
    },
    [documentId, policy, queryClient, workspaceId],
  );

  return {
    state,
    start,
    cancel: () => controller.current?.abort(),
    reset: () => setState({ phase: "idle" }),
    uploading: state.phase === "uploading",
  };
}
