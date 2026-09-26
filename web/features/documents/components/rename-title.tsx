"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Pencil } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { FormError } from "@/components/forms/form-error";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { useRenameDocument } from "@/features/documents/api/use-documents";
import {
  documentTitleSchema,
  type DocumentTitleValues,
} from "@/features/documents/schemas/document-schemas";
import type { Document } from "@/features/documents/types";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { announce } from "@/lib/a11y/announcer";
import { describeError } from "@/lib/api/describe-error";

/**
 * The document's title, editable in place for roles that may update documents.
 *
 * Saving sends the version this page last read (`expected_version`). If someone else
 * changed the document meanwhile the server answers 409; the optimistic change is rolled
 * back, the document is refetched, and the form says what happened -- *keeping what the
 * user typed*. Pressing Save again is then a deliberate overwrite of the newer title, with
 * the newer version number, rather than a silent one.
 */
export function RenameTitle({ document }: { document: Document }) {
  const { workspace, can } = useWorkspace();
  const rename = useRenameDocument(workspace.id);
  const [editing, setEditing] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);

  const form = useForm<DocumentTitleValues>({
    resolver: zodResolver(documentTitleSchema),
    defaultValues: { title: document.title },
  });

  if (!editing) {
    // The heading itself is rendered by the page; this is only the control to change it.
    if (!can("document:update")) return null;
    return (
      <Button
        size="sm"
        variant="ghost"
        className="text-fg-muted -ml-2.5"
        aria-label={`Rename ${document.title}`}
        onClick={() => {
          // Start from what the document is called *now*, not from an earlier edit.
          form.reset({ title: document.title });
          setFailure(null);
          setEditing(true);
        }}
      >
        <Pencil aria-hidden="true" />
        Rename
      </Button>
    );
  }

  async function onSubmit({ title }: DocumentTitleValues) {
    if (title === document.title) {
      setEditing(false);
      return;
    }
    setFailure(null);
    try {
      await rename.mutateAsync({ document, title });
      setEditing(false);
      announce(`Renamed to ${title}`);
    } catch (error) {
      setFailure(error);
    }
  }

  const description = failure ? describeError(failure) : null;

  return (
    <form
      method="post"
      onSubmit={form.handleSubmit(onSubmit)}
      noValidate
      className="max-w-xl font-sans"
    >
      <Field label="Document title" hideLabel error={form.formState.errors.title?.message}>
        {(control) => (
          <Input
            {...control}
            {...form.register("title")}
            autoFocus
            autoComplete="off"
            onKeyDown={(event) => {
              if (event.key === "Escape") setEditing(false);
            }}
          />
        )}
      </Field>

      <FormError
        className="mt-2"
        message={
          description
            ? description.kind === "conflict"
              ? "Someone else changed this document while you were editing. The page now shows their version. Save again to replace their title with yours, or cancel to keep theirs."
              : description.message
            : null
        }
        requestId={description?.requestId}
      />

      <div className="mt-2 flex gap-2">
        <Button type="submit" variant="primary" size="sm" loading={rename.isPending}>
          {rename.isPending ? "Saving" : "Save"}
        </Button>
        <Button size="sm" onClick={() => setEditing(false)} disabled={rename.isPending}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
