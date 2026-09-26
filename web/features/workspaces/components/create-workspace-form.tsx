"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { FormError } from "@/components/forms/form-error";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { useCreateWorkspace } from "@/features/workspaces/api/use-workspaces";
import {
  workspaceNameSchema,
  type WorkspaceNameValues,
} from "@/features/workspaces/schemas/workspace-schemas";
import { describeError } from "@/lib/api/describe-error";
import { isApiError } from "@/lib/api/errors";
import { applyServerErrors } from "@/lib/forms/server-errors";
import { routes } from "@/lib/navigation";

/** Creates a workspace and opens it. Used on the first-run screen and in the switcher's dialog. */
export function CreateWorkspaceForm({
  onCreated,
  onCancel,
  autoFocus = true,
}: {
  onCreated?: () => void;
  onCancel?: () => void;
  autoFocus?: boolean;
}) {
  const router = useRouter();
  const create = useCreateWorkspace();
  const [formError, setFormError] = useState<{ message: string; requestId: string } | null>(null);

  const form = useForm<WorkspaceNameValues>({
    resolver: zodResolver(workspaceNameSchema),
    defaultValues: { name: "" },
  });

  async function onSubmit({ name }: WorkspaceNameValues) {
    setFormError(null);
    try {
      const workspace = await create.mutateAsync(name);
      onCreated?.();
      router.push(routes.documents(workspace.id));
    } catch (error) {
      setFormError(
        applyServerErrors(error, form.setError, ["name"]) ?? {
          message: describeError(error).message,
          requestId: isApiError(error) ? error.requestId : "",
        },
      );
    }
  }

  return (
    <form method="post" onSubmit={form.handleSubmit(onSubmit)} noValidate className="space-y-4">
      <FormError message={formError?.message} requestId={formError?.requestId} />
      <Field
        label="Workspace name"
        description="For example, a team, a client, or a research topic."
        error={form.formState.errors.name?.message}
      >
        {(control) => (
          <Input
            {...control}
            {...form.register("name")}
            autoComplete="off"
            autoFocus={autoFocus}
            maxLength={200}
          />
        )}
      </Field>
      <div className="flex justify-end gap-2">
        {onCancel ? (
          <Button variant="secondary" onClick={onCancel} disabled={form.formState.isSubmitting}>
            Cancel
          </Button>
        ) : null}
        <Button type="submit" variant="primary" loading={form.formState.isSubmitting}>
          {form.formState.isSubmitting ? "Creating" : "Create workspace"}
        </Button>
      </div>
    </form>
  );
}
