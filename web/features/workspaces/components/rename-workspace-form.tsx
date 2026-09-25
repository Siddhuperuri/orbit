"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { FormError } from "@/components/forms/form-error";
import { notify } from "@/components/feedback/notify";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { useRenameWorkspace, useWorkspaceQuery } from "@/features/workspaces/api/use-workspaces";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import {
  workspaceNameSchema,
  type WorkspaceNameValues,
} from "@/features/workspaces/schemas/workspace-schemas";
import { describeError } from "@/lib/api/describe-error";
import { ErrorCode, isApiError } from "@/lib/api/errors";
import { applyServerErrors } from "@/lib/forms/server-errors";

/**
 * Renames the workspace using optimistic concurrency: the save carries the version
 * this page read, and if a colleague renamed it first the server refuses (409) and
 * the person is offered the current name instead of silently overwriting theirs.
 */
export function RenameWorkspaceForm() {
  const { workspace, can } = useWorkspace();
  const rename = useRenameWorkspace(workspace.id);
  const latest = useWorkspaceQuery(workspace.id);
  const [formError, setFormError] = useState<{ message: string; requestId: string } | null>(null);
  const [conflict, setConflict] = useState(false);

  const editable = can("workspace:update");
  const form = useForm<WorkspaceNameValues>({
    resolver: zodResolver(workspaceNameSchema),
    // `values` (not `defaultValues`) so the field follows the workspace when it changes.
    values: { name: workspace.name },
  });

  async function onSubmit({ name }: WorkspaceNameValues) {
    setFormError(null);
    setConflict(false);
    try {
      await rename.mutateAsync({ name, expectedVersion: workspace.version });
      notify.success("Workspace renamed");
    } catch (error) {
      if (isApiError(error) && error.code === ErrorCode.Conflict) {
        setConflict(true);
        return;
      }
      setFormError(
        applyServerErrors(error, form.setError, ["name"]) ?? {
          message: describeError(error).message,
          requestId: isApiError(error) ? error.requestId : "",
        },
      );
    }
  }

  return (
    <form onSubmit={form.handleSubmit(onSubmit)} noValidate className="max-w-md space-y-4">
      <FormError message={formError?.message} requestId={formError?.requestId} />

      {conflict ? (
        <div
          role="alert"
          className="border-warning/30 bg-warning-soft text-warning rounded-md border px-3 py-2.5 text-sm"
        >
          <p>Someone else changed this workspace while you were editing.</p>
          <Button
            size="sm"
            className="mt-2"
            loading={latest.isFetching}
            onClick={() => {
              setConflict(false);
              void latest.refetch();
            }}
          >
            Show the current name
          </Button>
        </div>
      ) : null}

      <Field
        label="Workspace name"
        error={form.formState.errors.name?.message}
        description={editable ? undefined : "Only admins and owners can rename a workspace."}
      >
        {(control) => (
          <Input
            {...control}
            {...form.register("name")}
            readOnly={!editable}
            autoComplete="off"
            maxLength={200}
          />
        )}
      </Field>

      {editable ? (
        <Button
          type="submit"
          variant="primary"
          loading={form.formState.isSubmitting}
          disabled={!form.formState.isDirty}
        >
          {form.formState.isSubmitting ? "Saving" : "Save name"}
        </Button>
      ) : null}
    </form>
  );
}
