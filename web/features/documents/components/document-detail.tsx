"use client";

import { ArchiveRestore, ArrowLeft, Archive, Trash2 } from "lucide-react";
import Link from "next/link";

import { notify } from "@/components/feedback/notify";
import { QueryBoundary } from "@/components/feedback/query-boundary";
import { PageContainer } from "@/components/layout/page-container";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { LoadingRegion, Skeleton } from "@/components/ui/skeleton";
import { useArchiveDocument, useDocument } from "@/features/documents/api/use-documents";
import { DocumentActions } from "@/features/documents/components/document-actions";
import { DocumentAskPanel } from "@/features/documents/components/document-ask-panel";
import { DocumentMetadata } from "@/features/documents/components/document-metadata";
import { DocumentViewer } from "@/features/documents/components/document-viewer";
import { FileKindIcon } from "@/features/documents/components/file-kind-icon";
import { ProcessingPanel } from "@/features/documents/components/processing-panel";
import { RenameTitle } from "@/features/documents/components/rename-title";
import { VersionsPanel } from "@/features/documents/components/versions-panel";
import { DocumentAvailability } from "@/features/documents/hooks/use-document-access";
import { useProcessingWatcher } from "@/features/documents/hooks/use-processing-watcher";
import { fileKindLabel, fileKindOf } from "@/features/documents/lib/file-kind";
import type { Document } from "@/features/documents/types";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { ErrorCode, isApiError } from "@/lib/api/errors";
import { useDocumentTitle } from "@/lib/hooks/use-document-title";
import { routes } from "@/lib/navigation";
import { cn } from "@/lib/utils/cn";
import { pluralize } from "@/lib/utils/format";

function DetailSkeleton() {
  return (
    <LoadingRegion label="Loading document">
      <Skeleton className="h-8 w-2/3" />
      <div className="mt-8 grid gap-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="space-y-6">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
        <div className="grid grid-cols-2 gap-6">
          {Array.from({ length: 6 }, (_, index) => (
            <div key={index} className="space-y-1.5">
              <Skeleton className="h-3 w-12" />
              <Skeleton className="h-4 w-24" />
            </div>
          ))}
        </div>
      </div>
    </LoadingRegion>
  );
}

function ArchivedBanner({ document }: { document: Document }) {
  const { workspace, can } = useWorkspace();
  const archive = useArchiveDocument(workspace.id);

  return (
    <div
      role="status"
      className="border-line-strong bg-canvas mb-8 flex flex-wrap items-center gap-x-4 gap-y-2 border px-5 py-4"
    >
      <Archive className="text-fg-muted size-4 shrink-0" aria-hidden="true" />
      <p className="text-fg min-w-0 flex-1 text-base">
        <span className="font-medium">This document is archived.</span> It&apos;s hidden from your
        document list and left out of search and answers. Nothing has been removed.
      </p>
      {can("document:update") ? (
        <Button
          size="sm"
          loading={archive.isPending}
          onClick={() =>
            archive.mutate(
              { documentId: document.id, archived: false },
              { onSuccess: () => notify.success(`Restored “${document.title}”`) },
            )
          }
        >
          <ArchiveRestore aria-hidden="true" />
          Restore
        </Button>
      ) : null}
    </div>
  );
}

/**
 * Shown when a document we are looking at stops existing for us -- deleted by someone
 * else, or access revoked -- discovered by a refresh. The last copy we hold stays on
 * screen so the reader is not yanked away mid-thought, but it is labelled, and nothing on
 * it can be acted on: every request would be a 404.
 */
function GoneBanner({ workspaceId }: { workspaceId: string }) {
  return (
    <div
      role="alert"
      className="border-danger/50 bg-canvas mb-8 flex flex-wrap items-center gap-x-4 gap-y-2 border px-5 py-4"
    >
      <Trash2 className="text-danger size-4 shrink-0" aria-hidden="true" />
      <p className="text-fg min-w-0 flex-1 text-base">
        <span className="font-medium">This document is no longer available.</span> It was deleted,
        or you no longer have access. What&apos;s shown is the last copy this page loaded.
      </p>
      <Button asChild size="sm" variant="primary">
        <Link href={routes.documents(workspaceId)}>Back to documents</Link>
      </Button>
    </div>
  );
}

/** A ruled panel: hairline frame, no fill -- the grid shows the structure. */
const CARD = "scroll-reveal border-line bg-surface/70 rounded-2xl border p-6";

function DocumentView({ document, gone }: { document: Document; gone: boolean }) {
  const { workspace } = useWorkspace();
  const version = document.current_version;
  const kind = fileKindOf(version);
  useDocumentTitle(document.title);
  // Follows the document while it is still being processed, and stops when it is done.
  // Not while it is gone: every poll would be a 404.
  useProcessingWatcher(workspace.id, gone ? [] : [document]);

  return (
    <DocumentAvailability gone={gone}>
      {gone ? <GoneBanner workspaceId={workspace.id} /> : null}
      {!gone && document.archived_at !== null ? <ArchivedBanner document={document} /> : null}

      <PageHeader
        eyebrow={
          <>
            <FileKindIcon kind={kind} className="size-6 [&_svg]:size-3.5" />
            {fileKindLabel(kind)}
            {version?.page_count ? ` · ${pluralize(version.page_count, "page")}` : null}
          </>
        }
        title={document.title}
        titleAction={gone ? null : <RenameTitle document={document} />}
        actions={gone ? null : <DocumentActions document={document} />}
      />

      {/* Reading on the first four columns, the record of the file on the last two. */}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem] xl:grid-cols-6 xl:gap-0">
        <div className="min-w-0 space-y-6 xl:col-span-4 xl:pr-8">
          <div className={CARD}>
            <ProcessingPanel document={document} />
          </div>
          {gone ? null : <DocumentAskPanel document={document} />}
          <div className={cn(CARD, "sm:px-10 sm:py-9")}>
            <DocumentViewer document={document} />
          </div>
        </div>
        <aside aria-label="Document information" className="min-w-0 space-y-6 xl:col-span-2">
          <div className={CARD}>
            <DocumentMetadata document={document} />
          </div>
          <div className={CARD}>
            <VersionsPanel document={document} />
          </div>
        </aside>
      </div>
    </DocumentAvailability>
  );
}

export function DocumentDetail({ documentId }: { documentId: string }) {
  const { workspace } = useWorkspace();
  const query = useDocument(workspace.id, documentId);

  const notFound = isApiError(query.error) && query.error.code === ErrorCode.NotFound;

  return (
    <PageContainer width="wide">
      <Link
        href={routes.documents(workspace.id)}
        className="label-micro text-fg-subtle hover:text-fg group mb-8 inline-flex items-center gap-2 rounded-xs transition-colors"
      >
        <ArrowLeft
          className="size-3.5 transition-transform duration-500 ease-out group-hover:-translate-x-1"
          aria-hidden="true"
        />
        All documents
      </Link>

      {notFound && !query.data ? (
        <div className="py-16 text-center">
          <h1 className="text-fg text-4xl font-semibold tracking-tight">Document not found</h1>
          <p className="text-fg-muted mt-1.5 text-base">
            It may have been deleted, or it may belong to a different workspace.
          </p>
          <Button asChild variant="primary" className="mt-5">
            <Link href={routes.documents(workspace.id)}>Back to documents</Link>
          </Button>
        </div>
      ) : notFound && query.data ? (
        <DocumentView document={query.data} gone />
      ) : (
        <QueryBoundary
          query={query}
          loading={<DetailSkeleton />}
          errorTitle="Couldn't load this document"
        >
          {(document) => <DocumentView document={document} gone={false} />}
        </QueryBoundary>
      )}
    </PageContainer>
  );
}
