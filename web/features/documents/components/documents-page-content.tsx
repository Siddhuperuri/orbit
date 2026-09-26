"use client";

import { Archive, FileText, FolderOpen, FolderPlus, Inbox, SearchX } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { EmptyState } from "@/components/feedback/empty-state";
import { QueryBoundary } from "@/components/feedback/query-boundary";
import { PageContainer } from "@/components/layout/page-container";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { useDocuments } from "@/features/documents/api/use-documents";
import { DocumentsTable } from "@/features/documents/components/documents-table";
import { DocumentsTableSkeleton } from "@/features/documents/components/documents-table-skeleton";
import { DocumentsToolbar } from "@/features/documents/components/documents-toolbar";
import { LibraryRail } from "@/features/documents/components/library-rail";
import { UploadButton } from "@/features/documents/components/upload-button";
import { UploadDropzone } from "@/features/documents/components/upload-dropzone";
import { UploadPanel } from "@/features/documents/components/upload-panel";
import { useFileDrop } from "@/features/documents/hooks/use-file-drop";
import { useProcessingWatcher } from "@/features/documents/hooks/use-processing-watcher";
import {
  isNarrowed,
  listStateToFilters,
  listStateToSearch,
  parseListState,
  toggleTag,
  withoutFilters,
  type DocumentListState,
} from "@/features/documents/lib/list-params";
import { useUploadQueue } from "@/features/documents/upload/upload-queue-provider";
import { useFolders } from "@/features/folders/api/use-folders";
import { FolderFormDialog } from "@/features/folders/components/folder-form-dialog";
import { pathLabel } from "@/features/folders/lib/tree";
import { useTags } from "@/features/tags/api/use-tags";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { useMediaQuery } from "@/lib/hooks/use-media-query";
import { cn } from "@/lib/utils/cn";
import { pluralize } from "@/lib/utils/format";

/**
 * The workspace's documents.
 *
 * Its whole state -- the folder or archive being viewed, the tag and status filters, the
 * title text, the sort -- is the URL, parsed here and written back on every change, so the
 * page keeps no copy that could disagree with the address: reload, back, and a shared link
 * all land in the same place. The list is fetched on the server with exactly those filters
 * and paged with a cursor; nothing is filtered or sorted over "what happens to be loaded".
 */
export function DocumentsPageContent() {
  const { workspace, can } = useWorkspace();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { add } = useUploadQueue();
  const wide = useMediaQuery("(min-width: 1024px)");
  const [creatingFolder, setCreatingFolder] = useState(false);

  const state = useMemo(() => parseListState(searchParams), [searchParams]);
  const filters = useMemo(() => listStateToFilters(state), [state]);

  const query = useDocuments(workspace.id, filters);
  const folders = useFolders(workspace.id);
  const tags = useTags(workspace.id);
  const documents = useMemo(
    () => query.data?.pages.flatMap((page) => page.items) ?? [],
    [query.data],
  );
  useProcessingWatcher(workspace.id, documents);

  const inArchive = state.archive === "archived";
  const folderId = state.folder.kind === "folder" ? state.folder.id : undefined;
  const folderName = folderId && folders.data ? pathLabel(folders.data, folderId) : null;
  // Uploading into the archive makes no sense: the document would arrive out of view.
  const canUpload = can("document:create") && !inArchive && state.folder.kind !== "unfiled";
  const canUploadHere = can("document:create") && !inArchive;

  const { dragging, dropProps } = useFileDrop((files) => {
    if (canUploadHere) add(files, { folderId });
  });

  function navigate(next: DocumentListState) {
    const search = listStateToSearch(next).toString();
    router.replace(search ? `${pathname}?${search}` : pathname, { scroll: false });
  }

  const title = inArchive
    ? "Archived"
    : state.folder.kind === "unfiled"
      ? "Unfiled"
      : folderId
        ? (folderName ?? "Folder")
        : "Documents";

  const description = inArchive
    ? "Out of the way, and out of search and answers. Restore a document to bring it back."
    : query.data
      ? `${pluralize(documents.length, "document")}${query.hasNextPage ? " loaded" : ""}`
      : undefined;

  const narrowed = isNarrowed(state);
  const filtered = state.q.trim() !== "" || state.status !== undefined || state.tags.length > 0;

  const rail = <LibraryRail state={state} onToggleTag={(id) => navigate(toggleTag(state, id))} />;

  return (
    <div
      {...dropProps}
      className={cn(
        "min-h-full transition-colors",
        dragging && canUploadHere && "bg-accent-soft/40",
      )}
    >
      <PageContainer width="wide">
        <PageHeader
          title={title}
          description={description}
          // On an empty, unfiltered list the empty state carries the same button as its
          // call to action; two identical buttons on one screen is noise.
          actions={
            <>
              {can("folder:write") && !wide ? (
                <Button onClick={() => setCreatingFolder(true)}>
                  <FolderPlus aria-hidden="true" />
                  New folder
                </Button>
              ) : null}
              {canUploadHere && (query.isPending || documents.length > 0 || narrowed) ? (
                <UploadButton folderId={folderId} />
              ) : null}
            </>
          }
        />

        {canUploadHere ? <UploadPanel workspaceId={workspace.id} /> : null}

        {/* On a desktop the rail is the first column of the grid and the list the other
            five, so both sit exactly between column rules. */}
        <div className="lg:flex lg:items-start lg:gap-8 xl:grid xl:grid-cols-6 xl:gap-0">
          {/* Not an <aside>: the rail is already a labelled <nav>, and a second unlabelled
              complementary landmark beside the shell's sidebar is one the reader can't tell apart. */}
          <div className="mb-5 lg:mb-0 lg:w-52 lg:shrink-0 xl:col-span-1 xl:w-auto xl:pr-4">
            {wide ? (
              <div className="sticky top-4">{rail}</div>
            ) : (
              <details className="border-line border">
                <summary className="label-caps text-fg cursor-pointer px-4 py-3">
                  Folders and tags
                </summary>
                <div className="border-line border-t p-2">{rail}</div>
              </details>
            )}
          </div>

          <div className="min-w-0 flex-1 xl:col-span-5">
            <DocumentsToolbar
              state={state}
              onChange={navigate}
              tags={tags.data}
              busy={query.isPlaceholderData}
            />

            <QueryBoundary
              query={query}
              loading={<DocumentsTableSkeleton />}
              errorTitle="Couldn't load documents"
              isEmpty={() => documents.length === 0}
              empty={
                filtered ? (
                  <EmptyState
                    icon={SearchX}
                    title="No documents match"
                    description="Nothing here fits those filters. Try fewer or different ones."
                    action={
                      <Button onClick={() => navigate(withoutFilters(state))}>Clear filters</Button>
                    }
                  />
                ) : inArchive ? (
                  <EmptyState
                    icon={Archive}
                    title="Nothing is archived"
                    description="Archive a document to put it out of the way without deleting it."
                  />
                ) : state.folder.kind === "unfiled" ? (
                  <EmptyState
                    icon={Inbox}
                    title="Every document is in a folder"
                    description="Documents that aren't in any folder appear here."
                  />
                ) : folderId ? (
                  <EmptyState
                    icon={FolderOpen}
                    title="This folder is empty"
                    description={
                      can("document:create")
                        ? "Upload a document here, or move existing ones into it."
                        : "Documents filed here will appear in this list."
                    }
                    action={canUpload ? <UploadButton folderId={folderId} /> : undefined}
                  />
                ) : canUpload ? (
                  <UploadDropzone />
                ) : (
                  <EmptyState
                    icon={FileText}
                    title="No documents yet"
                    description="Members with permission to upload can add documents to this workspace."
                  />
                )
              }
            >
              {() => (
                <>
                  <DocumentsTable
                    workspaceId={workspace.id}
                    documents={documents}
                    folders={folders.data}
                    sort={state.sort}
                    busy={query.isPlaceholderData}
                  />
                  {query.hasNextPage ? (
                    <div className="flex justify-center py-5">
                      <Button
                        onClick={() => void query.fetchNextPage()}
                        loading={query.isFetchingNextPage}
                      >
                        {query.isFetchingNextPage ? "Loading" : "Load more"}
                      </Button>
                    </div>
                  ) : null}
                </>
              )}
            </QueryBoundary>
          </div>
        </div>
      </PageContainer>

      <FolderFormDialog
        open={creatingFolder}
        onOpenChange={setCreatingFolder}
        mode={{ kind: "create", parent: null }}
      />
    </div>
  );
}
