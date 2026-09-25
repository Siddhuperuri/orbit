"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

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
import { Input } from "@/components/ui/input";
import { useCreateFolder, useRenameFolder } from "@/features/folders/api/use-folders";
import type { Folder } from "@/features/folders/types";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { announce } from "@/lib/a11y/announcer";
import { describeError } from "@/lib/api/describe-error";

const MAX_NAME = 255;

const schema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "A folder needs a name.")
    .max(MAX_NAME, `Use ${MAX_NAME} characters or fewer.`),
});
type Values = z.infer<typeof schema>;

export type FolderDialogMode =
  { kind: "create"; parent: Folder | null } | { kind: "rename"; folder: Folder };

/**
 * Create a folder (top level, or inside `parent`) or rename one.
 *
 * A rename sends the version this dialog last read, so if someone else renamed the
 * folder meanwhile the server answers 409 and the dialog says so and stays open --
 * with the tree already refetched behind it, so pressing Save again is a deliberate
 * overwrite of what is now there, not a silent one.
 */
export function FolderFormDialog({
  open,
  onOpenChange,
  mode,
  returnFocusRef,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: FolderDialogMode;
  /** The control to refocus on close when the dialog was opened from a menu. */
  returnFocusRef?: React.RefObject<HTMLElement | null>;
}) {
  const { workspace } = useWorkspace();
  const create = useCreateFolder(workspace.id);
  const rename = useRenameFolder(workspace.id);
  const pending = create.isPending || rename.isPending;

  const title =
    mode.kind === "rename"
      ? "Rename folder"
      : mode.parent
        ? `New folder in “${mode.parent.name}”`
        : "New folder";

  return (
    <Dialog open={open} onOpenChange={(next) => (pending ? undefined : onOpenChange(next))}>
      <DialogContent returnFocusRef={returnFocusRef}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {mode.kind === "create"
              ? "Folders keep related documents together. A folder can hold documents and other folders."
              : "Renaming doesn't move anything inside it."}
          </DialogDescription>
        </DialogHeader>
        {/* Rendered only while open, so each opening starts from the folder's current name with
            no leftover error -- without an effect to reset it. */}
        <FolderForm
          mode={mode}
          create={create}
          rename={rename}
          onClose={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  );
}

function FolderForm({
  mode,
  create,
  rename,
  onClose,
}: {
  mode: FolderDialogMode;
  create: ReturnType<typeof useCreateFolder>;
  rename: ReturnType<typeof useRenameFolder>;
  onClose: () => void;
}) {
  const [failure, setFailure] = useState<unknown>(null);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { name: mode.kind === "rename" ? mode.folder.name : "" },
  });
  const pending = create.isPending || rename.isPending;

  async function onSubmit({ name }: Values) {
    setFailure(null);
    try {
      if (mode.kind === "rename") {
        if (name === mode.folder.name) {
          onClose();
          return;
        }
        await rename.mutateAsync({
          folderId: mode.folder.id,
          name,
          expectedVersion: mode.folder.version,
        });
        announce(`Renamed to ${name}`);
      } else {
        await create.mutateAsync({ name, parentId: mode.parent?.id ?? null });
        announce(`Created folder ${name}`);
      }
      onClose();
    } catch (error) {
      setFailure(error);
    }
  }

  const description = failure ? describeError(failure) : null;

  return (
    <form onSubmit={form.handleSubmit(onSubmit)} noValidate className="space-y-4">
      <Field label="Name" error={form.formState.errors.name?.message}>
        {(control) => (
          <Input {...control} {...form.register("name")} autoFocus autoComplete="off" />
        )}
      </Field>
      <FormError message={description?.message} requestId={description?.requestId} />
      <DialogFooter>
        <Button onClick={onClose} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={pending}>
          {mode.kind === "rename" ? "Save" : "Create folder"}
        </Button>
      </DialogFooter>
    </form>
  );
}
