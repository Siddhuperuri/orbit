"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import { ConfirmDialog } from "@/components/feedback/confirm-dialog";
import { ErrorState } from "@/components/feedback/error-state";
import { notify } from "@/components/feedback/notify";
import { FormError } from "@/components/forms/form-error";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field } from "@/components/ui/field";
import { Input, NativeSelect } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useCreateTag, useDeleteTag, useTags, useUpdateTag } from "@/features/tags/api/use-tags";
import { TagChip } from "@/features/tags/components/tag-chip";
import { TAG_COLORS } from "@/features/tags/lib/colors";
import type { Tag, TagColor } from "@/features/tags/types";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { announce } from "@/lib/a11y/announcer";
import { describeError } from "@/lib/api/describe-error";
import { pluralize } from "@/lib/utils/format";

const MAX_NAME = 64;

const schema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "A tag needs a name.")
    .max(MAX_NAME, `Use ${MAX_NAME} characters or fewer.`),
  color: z.enum(["neutral", "accent", "success", "warning", "danger"]),
});
type Values = z.infer<typeof schema>;

function ColorSelect({
  value,
  onChange,
  id,
}: {
  value: TagColor;
  onChange: (color: TagColor) => void;
  id?: string;
}) {
  return (
    <NativeSelect
      id={id}
      value={value}
      onChange={(event) => onChange(event.target.value as TagColor)}
    >
      {TAG_COLORS.map(({ value: color, label }) => (
        <option key={color} value={color}>
          {label}
        </option>
      ))}
    </NativeSelect>
  );
}

function CreateTagForm() {
  const { workspace } = useWorkspace();
  const create = useCreateTag(workspace.id);
  const [failure, setFailure] = useState<unknown>(null);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { name: "", color: "neutral" },
  });
  const color = useWatch({ control: form.control, name: "color" });

  async function onSubmit(values: Values) {
    setFailure(null);
    try {
      await create.mutateAsync(values);
      announce(`Created tag ${values.name}`);
      form.reset({ name: "", color: values.color });
    } catch (error) {
      setFailure(error);
    }
  }

  const description = failure ? describeError(failure) : null;

  return (
    <form onSubmit={form.handleSubmit(onSubmit)} noValidate className="space-y-3">
      <div className="flex flex-wrap items-start gap-2">
        <Field
          label="New tag"
          className="min-w-40 flex-1"
          error={form.formState.errors.name?.message}
        >
          {(control) => <Input {...control} {...form.register("name")} autoComplete="off" />}
        </Field>
        <Field label="Colour" className="w-32">
          {(control) => (
            <ColorSelect
              id={control.id}
              value={color}
              onChange={(color) => form.setValue("color", color)}
            />
          )}
        </Field>
        <Button type="submit" variant="primary" loading={create.isPending} className="mt-[1.6rem]">
          Add tag
        </Button>
      </div>
      <FormError message={description?.message} requestId={description?.requestId} />
    </form>
  );
}

function TagRow({ tag, canWrite }: { tag: Tag; canWrite: boolean }) {
  const { workspace } = useWorkspace();
  const update = useUpdateTag(workspace.id);
  const remove = useDeleteTag(workspace.id);
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    values: { name: tag.name, color: tag.color },
  });
  const color = useWatch({ control: form.control, name: "color" });

  async function onSubmit(values: Values) {
    setFailure(null);
    const changes: { name?: string; color?: TagColor } = {};
    if (values.name !== tag.name) changes.name = values.name;
    if (values.color !== tag.color) changes.color = values.color;
    if (Object.keys(changes).length === 0) {
      setEditing(false);
      return;
    }
    try {
      await update.mutateAsync({ tagId: tag.id, changes, expectedVersion: tag.version });
      announce(`Updated tag ${values.name}`);
      setEditing(false);
    } catch (error) {
      setFailure(error);
    }
  }

  if (editing) {
    const description = failure ? describeError(failure) : null;
    return (
      <li className="py-3">
        <form onSubmit={form.handleSubmit(onSubmit)} noValidate className="space-y-2">
          <div className="flex flex-wrap items-start gap-2">
            <Field
              label={`Name of ${tag.name}`}
              hideLabel
              className="min-w-40 flex-1"
              error={form.formState.errors.name?.message}
            >
              {(control) => (
                <Input {...control} {...form.register("name")} autoFocus autoComplete="off" />
              )}
            </Field>
            <Field label={`Colour of ${tag.name}`} hideLabel className="w-32">
              {(control) => (
                <ColorSelect
                  id={control.id}
                  value={color}
                  onChange={(color) => form.setValue("color", color)}
                />
              )}
            </Field>
            <Button type="submit" variant="primary" size="md" loading={update.isPending}>
              Save
            </Button>
            <Button onClick={() => setEditing(false)} disabled={update.isPending}>
              Cancel
            </Button>
          </div>
          <FormError message={description?.message} requestId={description?.requestId} />
        </form>
      </li>
    );
  }

  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1.5 py-2.5">
      <span className="min-w-0 flex-1">
        <TagChip tag={tag} />
        <span className="text-fg-muted ml-2 text-sm">
          {pluralize(tag.document_count, "document")}
        </span>
      </span>
      {canWrite ? (
        <span className="flex gap-1">
          <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
            Edit <span className="sr-only">tag {tag.name}</span>
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setConfirming(true)}>
            Delete <span className="sr-only">tag {tag.name}</span>
          </Button>
        </span>
      ) : null}

      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={`Delete tag “${tag.name}”?`}
        description={
          tag.document_count > 0
            ? `It will be removed from ${pluralize(tag.document_count, "document")}. The documents themselves aren't affected.`
            : "No documents carry it, so nothing else is affected."
        }
        confirmLabel="Delete tag"
        pendingLabel="Deleting"
        onConfirm={async () => {
          await remove.mutateAsync(tag.id);
          notify.success(`Deleted tag “${tag.name}”`);
        }}
      />
    </li>
  );
}

/**
 * Every tag in the workspace, with how many documents carry it. People who may change
 * tags can add, rename, recolour, and delete them; everyone else can only read the list.
 * Deleting names its blast radius -- how many documents lose the tag -- before it happens.
 */
export function TagManagerDialog({
  open,
  onOpenChange,
  returnFocusRef,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The control to refocus on close, when the dialog was opened from a plain button. */
  returnFocusRef?: React.RefObject<HTMLElement | null>;
}) {
  const { workspace, can } = useWorkspace();
  const canWrite = can("tag:write");
  const tags = useTags(workspace.id);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl" returnFocusRef={returnFocusRef}>
        <DialogHeader>
          <DialogTitle>Tags</DialogTitle>
          <DialogDescription>
            Tags label documents across folders. They&apos;re shared by everyone in this workspace.
          </DialogDescription>
        </DialogHeader>

        {canWrite ? <CreateTagForm /> : null}

        {tags.isPending ? (
          <div className="space-y-3" role="status" aria-busy="true">
            <span className="sr-only">Loading tags…</span>
            <Skeleton className="h-6 w-1/2" />
            <Skeleton className="h-6 w-2/3" />
          </div>
        ) : tags.data === undefined ? (
          <ErrorState
            compact
            error={tags.error}
            title="Couldn't load tags"
            onRetry={() => void tags.refetch()}
            retrying={tags.isFetching}
          />
        ) : tags.data.length === 0 ? (
          <p className="text-fg-muted text-base">
            No tags yet.{canWrite ? " Add one above to start labelling documents." : ""}
          </p>
        ) : (
          <ul className="divide-line border-line max-h-80 divide-y overflow-y-auto border-y">
            {tags.data.map((tag) => (
              <TagRow key={tag.id} tag={tag} canWrite={canWrite} />
            ))}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  );
}
