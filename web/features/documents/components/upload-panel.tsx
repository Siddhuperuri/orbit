"use client";

import { useQueries } from "@tanstack/react-query";
import { AlertTriangle, Ban, X } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { Button } from "@/components/ui/button";
import { documentApi } from "@/features/documents/api/endpoints";
import { documentKeys } from "@/features/documents/api/keys";
import { useReprocessDocument } from "@/features/documents/api/use-documents";
import { LifecycleBadge, StatusBadge } from "@/features/documents/components/status-badge";
import { TransferProgress } from "@/features/documents/components/transfer-progress";
import { useProcessingWatcher } from "@/features/documents/hooks/use-processing-watcher";
import type { Document } from "@/features/documents/types";
import type { UploadItem } from "@/features/documents/upload/upload-queue";
import { useUploadQueue } from "@/features/documents/upload/upload-queue-provider";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { describeError } from "@/lib/api/describe-error";
import { routes } from "@/lib/navigation";
import { formatBytes } from "@/lib/utils/format";

/** What has become of an uploaded document -- its own state, not the upload's. */
function DocumentOutcome({
  item,
  document,
  workspaceId,
}: {
  item: UploadItem;
  document: Document;
  workspaceId: string;
}) {
  const { can } = useWorkspace();
  const reprocess = useReprocessDocument(workspaceId);
  const version = document.current_version;
  const failed = version?.status === "failed";

  return (
    <div className="space-y-1.5">
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        <StatusBadge status={version?.status ?? "pending"} stage={version?.processing_stage} />
        <span className="text-fg-muted">
          {item.deduplicated ? "Already in this workspace" : "Uploaded"}
        </span>
        <Link
          href={routes.document(workspaceId, document.id)}
          className="text-accent rounded-xs underline underline-offset-2"
        >
          Open <span className="sr-only">{document.title}</span>
        </Link>
      </p>

      {failed ? (
        <div className="text-danger flex items-start gap-1.5 text-sm">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          <span className="min-w-0 flex-1">
            {version?.failure_reason ?? "Processing failed."}{" "}
            {can("document:update") ? (
              <Button
                size="sm"
                className="ml-1"
                loading={reprocess.isPending}
                onClick={() => reprocess.mutate(document.id)}
              >
                {reprocess.isPending ? "Restarting" : "Try again"}
              </Button>
            ) : null}
          </span>
        </div>
      ) : null}
    </div>
  );
}

function UploadRow({
  item,
  document,
  workspaceId,
}: {
  item: UploadItem;
  document: Document | undefined;
  workspaceId: string;
}) {
  const { retry, cancel, dismiss } = useUploadQueue();
  const active = item.status === "queued" || item.status === "uploading";

  return (
    <li className="flex items-start gap-3 py-2.5">
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-fg truncate text-base font-medium">{item.fileName}</span>
          <span className="text-fg-muted shrink-0 text-sm tabular-nums">
            {formatBytes(item.size)}
          </span>
        </div>

        {item.status === "queued" ? (
          <p className="text-fg-muted flex items-center gap-2 text-sm">
            <LifecycleBadge state="queued" />
            Waiting for its turn…
          </p>
        ) : null}

        {item.status === "uploading" ? (
          <TransferProgress fileName={item.fileName} progress={item.progress} phase={item.phase} />
        ) : null}

        {item.status === "done" && document ? (
          <DocumentOutcome item={item} document={document} workspaceId={workspaceId} />
        ) : null}

        {item.status === "rejected" ? (
          <p className="text-danger flex items-start gap-1.5 text-sm">
            <Ban className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            <span>
              <span className="font-medium">Not uploaded.</span> {item.rejection?.message}
            </span>
          </p>
        ) : null}

        {item.status === "failed" ? (
          <p className="text-danger flex items-start gap-1.5 text-sm">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            <span>
              <span className="font-medium">Upload failed.</span>{" "}
              {describeError(item.error).message}
            </span>
          </p>
        ) : null}
      </div>

      <div className="flex shrink-0 items-center gap-1">
        {item.status === "failed" ? (
          <Button size="sm" onClick={() => retry(item.id)}>
            Retry <span className="sr-only">{item.fileName}</span>
          </Button>
        ) : null}
        <Button
          size="icon"
          variant="ghost"
          aria-label={active ? `Cancel upload of ${item.fileName}` : `Dismiss ${item.fileName}`}
          onClick={() => (active ? cancel(item.id) : dismiss(item.id))}
        >
          <X aria-hidden="true" />
        </Button>
      </div>
    </li>
  );
}

/**
 * Everything the user has sent, and what became of it: the file, its transfer, and -- once
 * the server has it -- the document's own processing state, live.
 *
 * "Live" is the point. An upload finishing is only the start; the row keeps following the
 * document (queued, processing, indexing, ready, or failed with the reason and a way to try
 * again) through the same self-terminating poll the rest of the app uses.
 */
export function UploadPanel({ workspaceId }: { workspaceId: string }) {
  const { items, clearFinished } = useUploadQueue();

  // Passive observers of each uploaded document's cache entry (`enabled: false` never
  // fetches). They exist so rows re-render when the watcher below updates the cache, and so
  // the watcher is fed the *current* documents rather than the snapshots the queue kept --
  // a snapshot is "queued" forever, which would fill the watcher's small quota with finished
  // documents and starve the ones actually in progress.
  const uploaded = useMemo(
    () => items.filter((item) => item.status === "done" && item.document),
    [items],
  );
  const live = useQueries({
    queries: uploaded.map((item) => ({
      queryKey: documentKeys.detail(workspaceId, item.document!.id),
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        documentApi.get(workspaceId, item.document!.id, signal),
      initialData: item.document,
      enabled: false,
    })),
  });
  const liveDocuments = useMemo(() => {
    const byId = new Map<string, Document>();
    for (const result of live) if (result.data) byId.set(result.data.id, result.data);
    return byId;
  }, [live]);

  useProcessingWatcher(workspaceId, [...liveDocuments.values()]);

  if (items.length === 0) return null;
  const hasFinished = items.some((item) => item.status !== "queued" && item.status !== "uploading");

  return (
    <section aria-label="Uploads" className="border-line bg-canvas mb-8 border px-4 py-2">
      <div className="flex items-center justify-between py-1.5">
        <h2 className="text-fg text-sm font-semibold">Uploads</h2>
        {hasFinished ? (
          <Button size="sm" variant="ghost" onClick={clearFinished}>
            Clear finished
          </Button>
        ) : null}
      </div>
      <ul className="divide-line divide-y">
        {items.map((item) => (
          <UploadRow
            key={item.id}
            item={item}
            document={
              item.document ? (liveDocuments.get(item.document.id) ?? item.document) : undefined
            }
            workspaceId={workspaceId}
          />
        ))}
      </ul>
    </section>
  );
}
