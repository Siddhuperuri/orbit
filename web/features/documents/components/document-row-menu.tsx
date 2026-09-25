"use client";

import {
  Archive,
  ArchiveRestore,
  Download,
  FolderInput,
  MoreHorizontal,
  Trash2,
} from "lucide-react";
import { useRef, useState } from "react";

import { ConfirmDialog } from "@/components/feedback/confirm-dialog";
import { notify } from "@/components/feedback/notify";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  useArchiveDocument,
  useDeleteDocument,
  useDownloadDocument,
} from "@/features/documents/api/use-documents";
import { MoveDocumentDialog } from "@/features/documents/components/move-document-dialog";
import type { Document } from "@/features/documents/types";
import { useUploadQueue } from "@/features/documents/upload/upload-queue-provider";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";

/**
 * Per-row actions, each offered only to roles that hold the permission it needs:
 * everyone can download; `document:update` may move and archive; `document:delete` may
 * delete. A viewer's menu is just Download -- there is nothing on it that would only
 * produce a refusal.
 */
export function DocumentRowMenu({ document }: { document: Document }) {
  const { workspace, can } = useWorkspace();
  const { forgetDocument } = useUploadQueue();
  const download = useDownloadDocument(workspace.id);
  const archive = useArchiveDocument(workspace.id);
  const remove = useDeleteDocument(workspace.id);
  const [dialog, setDialog] = useState<"delete" | "move" | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const archived = document.archived_at !== null;
  const canUpdate = can("document:update");
  const canDelete = can("document:delete");

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            ref={triggerRef}
            type="button"
            aria-label={`Actions for ${document.title}`}
            className="text-fg-muted hover:bg-line/60 hover:text-fg inline-flex size-8 items-center justify-center rounded-md pointer-coarse:size-11"
          >
            <MoreHorizontal className="size-4" aria-hidden="true" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            disabled={document.current_version === null}
            onSelect={() => download.mutate(document.id)}
          >
            <Download aria-hidden="true" />
            Download
          </DropdownMenuItem>

          {canUpdate ? (
            <>
              <DropdownMenuItem onSelect={() => setDialog("move")}>
                <FolderInput aria-hidden="true" />
                Move to folder…
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() =>
                  archive.mutate(
                    { documentId: document.id, archived: !archived },
                    {
                      onSuccess: () =>
                        notify.success(
                          archived
                            ? `Restored “${document.title}”`
                            : `Archived “${document.title}”`,
                          {
                            description: archived
                              ? undefined
                              : "It's out of search and answers until you restore it.",
                          },
                        ),
                    },
                  )
                }
              >
                {archived ? <ArchiveRestore aria-hidden="true" /> : <Archive aria-hidden="true" />}
                {archived ? "Restore" : "Archive"}
              </DropdownMenuItem>
            </>
          ) : null}

          {canDelete ? (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem destructive onSelect={() => setDialog("delete")}>
                <Trash2 aria-hidden="true" />
                Delete…
              </DropdownMenuItem>
            </>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>

      {canUpdate ? (
        <MoveDocumentDialog
          document={document}
          open={dialog === "move"}
          onOpenChange={(open) => setDialog(open ? "move" : null)}
          returnFocusRef={triggerRef}
        />
      ) : null}

      {canDelete ? (
        <ConfirmDialog
          returnFocusRef={triggerRef}
          open={dialog === "delete"}
          onOpenChange={(open) => setDialog(open ? "delete" : null)}
          title={`Delete “${document.title}”?`}
          description="It will stop appearing in search and in answers immediately. This can't be undone. To keep it but put it out of the way, archive it instead."
          confirmLabel="Delete document"
          pendingLabel="Deleting"
          onConfirm={async () => {
            await remove.mutateAsync(document.id);
            forgetDocument(document.id);
            notify.success(`Deleted “${document.title}”`);
          }}
        />
      ) : null}
    </>
  );
}
