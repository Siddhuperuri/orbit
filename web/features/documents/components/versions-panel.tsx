"use client";

import { AlertTriangle, Ban, Download, Upload } from "lucide-react";
import Link from "next/link";
import { useRef } from "react";

import { ErrorState } from "@/components/feedback/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDocumentVersions, useDownloadVersion } from "@/features/documents/api/use-documents";
import { StatusBadge } from "@/features/documents/components/status-badge";
import { TransferProgress } from "@/features/documents/components/transfer-progress";
import { useDocumentAccess } from "@/features/documents/hooks/use-document-access";
import { useVersionUpload } from "@/features/documents/hooks/use-version-upload";
import { isInProgress } from "@/features/documents/status";
import type { Document, DocumentVersion } from "@/features/documents/types";
import { useUploadPolicy } from "@/features/documents/upload/use-upload-policy";
import { acceptAttribute } from "@/features/documents/upload/validate";
import { useUser } from "@/features/auth/hooks/use-user";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { describeError } from "@/lib/api/describe-error";
import { routes } from "@/lib/navigation";
import { formatBytes, formatDateTime, shortId } from "@/lib/utils/format";

/** Who uploaded a version. The API knows ids, not names, so the honest options are these. */
function uploaderLabel(userId: string | null, currentUserId: string): string {
  if (userId === null) return "a deleted account";
  if (userId === currentUserId) return "you";
  return `member ${shortId(userId)}`;
}

function VersionRow({ version, document }: { version: DocumentVersion; document: Document }) {
  // The list is a cached page of history; the document is live (it is being polled while
  // processing). For the current version, trust the live one -- otherwise this row keeps saying
  // "Queued" after the stepper above it says "Ready".
  const shown = version.is_current && document.current_version ? document.current_version : version;
  const { workspace } = useWorkspace();
  const { canDownload } = useDocumentAccess();
  const user = useUser();
  const download = useDownloadVersion(workspace.id);

  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-fg text-base font-medium">Version {version.version_number}</span>
        {version.is_current ? <Badge tone="accent">Current</Badge> : null}
        {version.is_current ? (
          <StatusBadge status={shown.status} stage={shown.processing_stage} />
        ) : null}
      </div>
      <p className="text-fg-muted mt-0.5 text-sm [overflow-wrap:anywhere]">
        {version.original_filename} · {formatBytes(version.byte_size)}
      </p>
      <p className="text-fg-muted text-sm">
        <time dateTime={version.created_at}>{formatDateTime(version.created_at)}</time> · by{" "}
        {uploaderLabel(version.created_by_user_id, user.id)}
      </p>
      {canDownload ? (
        <Button
          size="sm"
          variant="ghost"
          className="mt-1 -ml-2"
          loading={download.isPending}
          onClick={() => download.mutate({ documentId: document.id, versionId: version.id })}
        >
          <Download aria-hidden="true" />
          Download <span className="sr-only">version {version.version_number}</span>
        </Button>
      ) : null}
    </li>
  );
}

function NewVersion({ document }: { document: Document }) {
  const { workspace } = useWorkspace();
  const upload = useVersionUpload(workspace.id, document.id);
  const policy = useUploadPolicy();
  const input = useRef<HTMLInputElement>(null);
  const { state } = upload;

  return (
    <div className="border-line bg-surface mb-3 space-y-2 rounded-lg border p-3">
      <input
        ref={input}
        type="file"
        accept={acceptAttribute(policy)}
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        data-testid="version-input"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) void upload.start(file);
        }}
      />

      {state.phase === "uploading" ? (
        <>
          <p className="text-fg truncate text-sm font-medium">{state.fileName}</p>
          <TransferProgress
            fileName={state.fileName}
            progress={state.progress}
            phase={state.step}
          />
          <Button size="sm" onClick={upload.cancel}>
            Cancel
          </Button>
        </>
      ) : (
        <>
          <Button size="sm" onClick={() => input.current?.click()}>
            <Upload aria-hidden="true" />
            Upload a new version…
          </Button>
          <p className="text-fg-muted text-sm">
            Replaces the current version. Its text is re-read and re-indexed; the old file stays
            downloadable.
          </p>
        </>
      )}

      {state.phase === "rejected" ? (
        <p role="alert" className="text-danger flex items-start gap-1.5 text-sm">
          <Ban className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          <span>
            <span className="font-medium">Not uploaded.</span> {state.rejection.message}
          </span>
        </p>
      ) : null}

      {state.phase === "failed" ? (
        <p role="alert" className="text-danger flex items-start gap-1.5 text-sm">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          <span>
            <span className="font-medium">Upload failed.</span> {describeError(state.error).message}{" "}
            The document is unchanged.
          </span>
        </p>
      ) : null}

      {state.phase === "duplicate" ? (
        <p role="status" className="text-fg-muted text-sm">
          {state.fileName} is identical to{" "}
          <Link
            href={routes.document(workspace.id, state.existing.id)}
            className="text-accent rounded-xs underline underline-offset-2"
          >
            {state.existing.title}
          </Link>
          , which is already in this workspace. Nothing was changed here.
        </p>
      ) : null}

      {/* Only while it is still true: once processing has settled the stepper says the rest. */}
      {state.phase === "done" && isInProgress(document) ? (
        <p role="status" className="text-success text-sm">
          Uploaded{state.versionNumber ? ` as version ${state.versionNumber}` : ""}. It&apos;s being
          processed now.
        </p>
      ) : null}
    </div>
  );
}

/**
 * A document's revisions, newest first. Anyone who can read the document can download
 * any version -- the file of a superseded version is kept. Only people who may update the
 * document are offered the upload.
 *
 * What is *not* kept is the text: a superseded version's passages are removed so search
 * and answers never quote what the document no longer says. So the history shows files
 * and who uploaded them, and says so, rather than implying every version can be read here.
 */
export function VersionsPanel({ document }: { document: Document }) {
  const { workspace } = useWorkspace();
  const { canUpdate } = useDocumentAccess();
  const versions = useDocumentVersions(workspace.id, document.id, document.current_version?.id);
  const items = versions.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <section aria-labelledby="versions-heading">
      <h2 id="versions-heading" className="label-micro text-fg mb-4">
        Versions
      </h2>

      {canUpdate ? <NewVersion document={document} /> : null}

      {versions.isPending ? (
        <div role="status" aria-busy="true" className="space-y-3">
          <span className="sr-only">Loading versions…</span>
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-3 w-3/4" />
        </div>
      ) : versions.data === undefined ? (
        <ErrorState
          compact
          error={versions.error}
          title="Couldn't load versions"
          onRetry={() => void versions.refetch()}
          retrying={versions.isFetching}
        />
      ) : (
        <>
          <ol className="divide-line border-line divide-y border-y">
            {items.map((version) => (
              <VersionRow key={version.id} version={version} document={document} />
            ))}
          </ol>
          {versions.hasNextPage ? (
            <Button
              size="sm"
              className="mt-2"
              loading={versions.isFetchingNextPage}
              onClick={() => void versions.fetchNextPage()}
            >
              Show older versions
            </Button>
          ) : null}
          {items.length > 1 ? (
            <p className="text-fg-subtle mt-3 text-xs">
              Earlier versions keep their file, but their text is no longer searchable or readable
              here.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
