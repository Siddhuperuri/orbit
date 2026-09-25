"use client";

import Link from "next/link";

import { ConfirmDialog } from "@/components/feedback/confirm-dialog";
import { notify } from "@/components/feedback/notify";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { documentsHref, DEFAULT_LIST_STATE } from "@/features/documents/lib/list-params";
import { useDeleteFolder } from "@/features/folders/api/use-folders";
import { contentsOf, isEmpty } from "@/features/folders/lib/tree";
import type { Folder } from "@/features/folders/types";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { pluralize } from "@/lib/utils/format";

/**
 * Deleting a folder is only possible when it is empty, and this says so *before* asking
 * -- with what is in the way -- instead of offering a delete button that can only fail.
 *
 * ORBIT never moves or removes what is inside a folder as a side effect of deleting it:
 * silently reshuffling someone's organisation, or destroying documents, are both worse
 * than a refusal. The counts come from the tree the user is looking at, so they can be a
 * moment stale; the server re-checks under a lock, and if it disagrees the error is shown
 * in the dialog and the tree refreshes.
 */
export function DeleteFolderDialog({
  folder,
  open,
  onOpenChange,
  returnFocusRef,
}: {
  folder: Folder;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  returnFocusRef?: React.RefObject<HTMLElement | null>;
}) {
  const { workspace } = useWorkspace();
  const remove = useDeleteFolder(workspace.id);

  if (isEmpty(folder)) {
    return (
      <ConfirmDialog
        returnFocusRef={returnFocusRef}
        open={open}
        onOpenChange={onOpenChange}
        title={`Delete folder “${folder.name}”?`}
        description="It's empty, so no documents are affected."
        confirmLabel="Delete folder"
        pendingLabel="Deleting"
        onConfirm={async () => {
          await remove.mutateAsync(folder.id);
          notify.success(`Deleted folder “${folder.name}”`);
        }}
      />
    );
  }

  const { documents, subfolders } = contentsOf(folder);
  const parts = [
    documents > 0
      ? `${pluralize(documents, "document")}${
          folder.archived_document_count > 0 ? ` (${folder.archived_document_count} archived)` : ""
        }`
      : null,
    subfolders > 0 ? pluralize(subfolders, "subfolder") : null,
  ].filter(Boolean);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent returnFocusRef={returnFocusRef}>
        <DialogHeader>
          <DialogTitle>“{folder.name}” isn&apos;t empty</DialogTitle>
          <DialogDescription>
            It still holds {parts.join(" and ")}. Move or delete{" "}
            {documents + subfolders === 1 ? "it" : "them"} first &mdash; ORBIT won&apos;t move or
            remove anything for you.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button onClick={() => onOpenChange(false)}>Close</Button>
          {documents > 0 ? (
            <Button asChild variant="primary" onClick={() => onOpenChange(false)}>
              <Link
                href={documentsHref(workspace.id, {
                  ...DEFAULT_LIST_STATE,
                  folder: { kind: "folder", id: folder.id },
                  archive: folder.document_count === 0 ? "archived" : "active",
                })}
              >
                Show its documents
              </Link>
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
