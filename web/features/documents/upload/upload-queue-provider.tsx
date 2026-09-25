"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import { notify } from "@/components/feedback/notify";
import { refreshOrganization } from "@/features/documents/api/cache";
import { documentApi } from "@/features/documents/api/endpoints";
import { UploadQueue, type UploadItem } from "@/features/documents/upload/upload-queue";
import { useUploadPolicy } from "@/features/documents/upload/use-upload-policy";
import { validateFile } from "@/features/documents/upload/validate";
import { announce } from "@/lib/a11y/announcer";
import { describeError } from "@/lib/api/describe-error";

interface UploadQueueContextValue {
  items: readonly UploadItem[];
  add: (files: readonly File[], options?: { folderId?: string }) => void;
  retry: (id: string) => void;
  cancel: (id: string) => void;
  dismiss: (id: string) => void;
  clearFinished: () => void;
  forgetDocument: (documentId: string) => void;
}

const UploadQueueContext = createContext<UploadQueueContextValue | null>(null);

/**
 * Owns the workspace's upload queue for as long as the workspace is open, so that
 * moving between Documents, Search, and Chat does not interrupt a transfer.
 * Closing the tab still would, which is why leaving with an upload running asks
 * for confirmation.
 */
export function UploadQueueProvider({
  workspaceId,
  children,
}: {
  workspaceId: string;
  children: ReactNode;
}) {
  const queryClient = useQueryClient();

  const policy = useUploadPolicy();

  // One queue per workspace, created once. `useState` (not `useMemo`) because
  // React may discard a memoised value, and a discarded queue is a lost upload.
  const [queue] = useState(
    () =>
      new UploadQueue({
        upload: (file, { onProgress, signal, folderId }) =>
          documentApi.upload(workspaceId, { file, folderId, onProgress, signal }),
        onDone: (item) => {
          // New documents change the list, the folder counts, and (via dedup) nothing else.
          void refreshOrganization(queryClient, workspaceId);
          if (item.deduplicated) {
            // The toast is a live region; announcing "uploaded" as well would contradict it.
            notify.info(`${item.fileName} is already in this workspace`, {
              description: "The same content was uploaded before, so nothing new was added.",
            });
          } else {
            announce(`${item.fileName} uploaded`);
          }
        },
        onFailed: (item) => {
          announce(
            `${item.fileName} failed to upload. ${describeError(item.error).message}`,
            "assertive",
          );
          notify.error(item.error, { title: `Couldn't upload ${item.fileName}` });
        },
        onRejected: (items) => {
          // The rows say why, but a screen-reader user is not looking at them.
          announce(
            items.length === 1
              ? (items[0]?.rejection?.message ?? "A file was not added.")
              : `${items.length} files were not added. See the uploads list for why.`,
            "assertive",
          );
        },
      }),
  );

  // The queue is created once, but the policy arrives later (from `/meta`) and can change:
  // hand it the current rule whenever it does, so files added after it loads are checked
  // against it without recreating the queue (which would drop whatever is uploading).
  useEffect(() => {
    queue.setValidator((file) => validateFile(file, policy));
  }, [queue, policy]);

  const items = useSyncExternalStore(queue.subscribe, queue.getSnapshot, queue.getSnapshot);
  const active = items.some((item) => item.status === "queued" || item.status === "uploading");

  useEffect(() => {
    if (!active) return;
    // Browsers ignore the message and show their own; setting returnValue is
    // what makes the prompt appear.
    const guard = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [active]);

  const value = useMemo<UploadQueueContextValue>(
    () => ({
      items,
      add: (files, options) => queue.add(files, options),
      retry: (id) => queue.retry(id),
      cancel: (id) => queue.cancel(id),
      dismiss: (id) => queue.dismiss(id),
      clearFinished: () => queue.clearFinished(),
      forgetDocument: (documentId) => queue.forgetDocument(documentId),
    }),
    [items, queue],
  );

  return <UploadQueueContext.Provider value={value}>{children}</UploadQueueContext.Provider>;
}

export function useUploadQueue(): UploadQueueContextValue {
  const value = useContext(UploadQueueContext);
  if (!value) throw new Error("useUploadQueue must be used inside <UploadQueueProvider>.");
  return value;
}
