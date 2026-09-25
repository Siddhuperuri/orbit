"use client";

import { useState } from "react";

import { ErrorState } from "@/components/feedback/error-state";
import { notify } from "@/components/feedback/notify";
import { FormError } from "@/components/forms/form-error";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/skeleton";
import { useMoveDocument } from "@/features/documents/api/use-documents";
import type { Document } from "@/features/documents/types";
import { useFolders } from "@/features/folders/api/use-folders";
import { FolderSelect } from "@/features/folders/components/folder-select";
import { pathLabel } from "@/features/folders/lib/tree";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { describeError } from "@/lib/api/describe-error";

/**
 * File a document into a folder, or take it out of one.
 *
 * Sends the document's version, so if it changed since this page read it the server says
 * so (and this dialog shows it, staying open with the document already refetched) instead
 * of quietly overwriting someone else's edit. If the chosen folder was deleted meanwhile
 * the answer is "no longer exists", and the folder list behind the dialog refreshes.
 */
export function MoveDocumentDialog({
  document,
  open,
  onOpenChange,
  returnFocusRef,
}: {
  document: Document;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  returnFocusRef?: React.RefObject<HTMLElement | null>;
}) {
  const { workspace } = useWorkspace();
  const move = useMoveDocument(workspace.id);

  return (
    <Dialog open={open} onOpenChange={(next) => (move.isPending ? undefined : onOpenChange(next))}>
      <DialogContent returnFocusRef={returnFocusRef}>
        <DialogHeader>
          <DialogTitle>Move “{document.title}”</DialogTitle>
          <DialogDescription>Choose where this document should live.</DialogDescription>
        </DialogHeader>
        {/* Rendered only while open, so each opening starts from the document's current folder
            with no leftover error -- without an effect to reset it. */}
        <MoveForm document={document} move={move} onClose={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

function MoveForm({
  document,
  move,
  onClose,
}: {
  document: Document;
  move: ReturnType<typeof useMoveDocument>;
  onClose: () => void;
}) {
  const { workspace } = useWorkspace();
  const folders = useFolders(workspace.id);
  const [target, setTarget] = useState<string | null>(document.folder_id);
  const [failure, setFailure] = useState<unknown>(null);

  const unchanged = target === document.folder_id;
  const description = failure ? describeError(failure) : null;

  async function save() {
    setFailure(null);
    try {
      const updated = await move.mutateAsync({ document, folderId: target });
      const where =
        updated.folder_id && folders.data
          ? `“${pathLabel(folders.data, updated.folder_id) ?? "the folder"}”`
          : "no folder";
      // The toast region is a polite live region, so this is announced without a second call.
      notify.success(`Moved “${updated.title}” to ${where}`);
      onClose();
    } catch (error) {
      setFailure(error);
    }
  }

  return (
    <>
      {folders.isPending ? (
        <div role="status" aria-busy="true">
          <span className="sr-only">Loading folders…</span>
          <Skeleton className="h-8 w-full" />
        </div>
      ) : folders.data === undefined ? (
        <ErrorState
          compact
          error={folders.error}
          title="Couldn't load folders"
          onRetry={() => void folders.refetch()}
          retrying={folders.isFetching}
        />
      ) : (
        <Field label="Folder">
          {(control) => (
            <FolderSelect
              {...control}
              folders={folders.data}
              value={target}
              onChange={setTarget}
              noneLabel="No folder (unfiled)"
            />
          )}
        </Field>
      )}

      <FormError message={description?.message} requestId={description?.requestId} />

      <DialogFooter>
        <Button onClick={onClose} disabled={move.isPending}>
          Cancel
        </Button>
        <Button
          variant="primary"
          onClick={() => void save()}
          loading={move.isPending}
          disabled={unchanged || folders.data === undefined}
        >
          Move
        </Button>
      </DialogFooter>
    </>
  );
}
