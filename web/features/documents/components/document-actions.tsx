"use client";

import {
  Archive,
  ArchiveRestore,
  Download,
  FolderInput,
  MoreHorizontal,
  Search,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { ConfirmDialog } from "@/components/feedback/confirm-dialog";
import { notify } from "@/components/feedback/notify";
import { Button } from "@/components/ui/button";
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
import { statusOf } from "@/features/documents/status";
import type { Document } from "@/features/documents/types";
import { useUploadQueue } from "@/features/documents/upload/upload-queue-provider";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { routes } from "@/lib/navigation";

/**
 * What can be done with a document. Every action appears only for roles that may perform
 * it, so a viewer sees Download and Search and nothing that would only be refused.
 *
 * Deleting is the one irreversible action, so it is confirmed by name and says what the
 * gentler alternative is -- archiving keeps everything and is undone with one click.
 */
export function DocumentActions({ document }: { document: Document }) {
  const { workspace, can } = useWorkspace();
  const { forgetDocument } = useUploadQueue();
  const router = useRouter();
  const download = useDownloadDocument(workspace.id);
  const archive = useArchiveDocument(workspace.id);
  const remove = useDeleteDocument(workspace.id);
  const [dialog, setDialog] = useState<"delete" | "move" | null>(null);
  const menuTrigger = useRef<HTMLButtonElement>(null);

  const archived = document.archived_at !== null;
  const searchable = statusOf(document) === "ready" && !archived;
  const canUpdate = can("document:update");
  const canDelete = can("document:delete");

  return (
    <>
      <Button
        loading={download.isPending}
        disabled={document.current_version === null}
        onClick={() => download.mutate(document.id)}
      >
        <Download aria-hidden="true" />
        Download
      </Button>

      {searchable && can("search:query") ? (
        <Button asChild>
          <Link href={routes.search(workspace.id, { doc: document.id })}>
            <Search aria-hidden="true" />
            Search within
          </Link>
        </Button>
      ) : null}

      {canUpdate || canDelete ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="secondary" size="icon" ref={menuTrigger} aria-label="More actions">
              <MoreHorizontal aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
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
                          ),
                      },
                    )
                  }
                >
                  {archived ? (
                    <ArchiveRestore aria-hidden="true" />
                  ) : (
                    <Archive aria-hidden="true" />
                  )}
                  {archived ? "Restore" : "Archive"}
                </DropdownMenuItem>
              </>
            ) : null}
            {canDelete ? (
              <>
                {canUpdate ? <DropdownMenuSeparator /> : null}
                <DropdownMenuItem destructive onSelect={() => setDialog("delete")}>
                  <Trash2 aria-hidden="true" />
                  Delete…
                </DropdownMenuItem>
              </>
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}

      {canUpdate ? (
        <MoveDocumentDialog
          document={document}
          open={dialog === "move"}
          onOpenChange={(open) => setDialog(open ? "move" : null)}
          returnFocusRef={menuTrigger}
        />
      ) : null}

      {canDelete ? (
        <ConfirmDialog
          returnFocusRef={menuTrigger}
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
            router.replace(routes.documents(workspace.id));
          }}
        />
      ) : null}
    </>
  );
}
