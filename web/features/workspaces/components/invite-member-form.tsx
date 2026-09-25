"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";

import { FormError } from "@/components/forms/form-error";
import { notify } from "@/components/feedback/notify";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input, NativeSelect } from "@/components/ui/input";
import { useInviteMember } from "@/features/workspaces/api/use-workspaces";
import { ROLE_DESCRIPTIONS, ROLE_LABELS } from "@/features/workspaces/permissions";
import {
  inviteMemberSchema,
  type InviteMemberValues,
} from "@/features/workspaces/schemas/workspace-schemas";
import { describeError } from "@/lib/api/describe-error";
import { isApiError } from "@/lib/api/errors";
import { applyServerErrors } from "@/lib/forms/server-errors";

const ASSIGNABLE = ["viewer", "member", "admin"] as const;

/**
 * Adds an existing ORBIT account to the workspace, by account ID.
 *
 * The API has no user directory or lookup-by-email, so the ID is what an admin has
 * to work with; a person finds theirs under Account. That is a limitation of the
 * current backend, and this form says so plainly instead of pretending an email
 * field would work.
 */
export function InviteMemberForm({ workspaceId }: { workspaceId: string }) {
  const invite = useInviteMember(workspaceId);
  const [formError, setFormError] = useState<{ message: string; requestId: string } | null>(null);

  const form = useForm<InviteMemberValues>({
    resolver: zodResolver(inviteMemberSchema),
    defaultValues: { user_id: "", role: "member" },
  });
  const role = useWatch({ control: form.control, name: "role" });

  async function onSubmit(values: InviteMemberValues) {
    setFormError(null);
    try {
      await invite.mutateAsync({ userId: values.user_id, role: values.role });
      notify.success("Member added");
      form.reset({ user_id: "", role: values.role });
    } catch (error) {
      setFormError(
        applyServerErrors(error, form.setError, ["user_id", "role"]) ?? {
          message: describeError(error).message,
          requestId: isApiError(error) ? error.requestId : "",
        },
      );
    }
  }

  return (
    <form onSubmit={form.handleSubmit(onSubmit)} noValidate className="max-w-xl space-y-4">
      <FormError message={formError?.message} requestId={formError?.requestId} />

      <Field
        label="Account ID"
        description="Ask them to copy it from Account settings."
        error={form.formState.errors.user_id?.message}
      >
        {(control) => (
          <Input
            {...control}
            {...form.register("user_id")}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            className="font-mono text-sm"
          />
        )}
      </Field>

      <Field
        label="Role"
        description={ROLE_DESCRIPTIONS[role]}
        error={form.formState.errors.role?.message}
      >
        {(control) => (
          <NativeSelect {...control} {...form.register("role")} className="w-40">
            {ASSIGNABLE.map((value) => (
              <option key={value} value={value}>
                {ROLE_LABELS[value]}
              </option>
            ))}
          </NativeSelect>
        )}
      </Field>

      <Button type="submit" variant="primary" loading={form.formState.isSubmitting}>
        {form.formState.isSubmitting ? "Adding" : "Add member"}
      </Button>
    </form>
  );
}
