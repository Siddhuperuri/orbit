"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { FormError } from "@/components/forms/form-error";
import { PasswordInput } from "@/components/forms/password-input";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { useConfirmPasswordReset } from "@/features/auth/api/use-auth-mutations";
import { AuthCard } from "@/features/auth/components/auth-card";
import {
  MIN_PASSWORD_LENGTH,
  resetPasswordSchema,
  type ResetPasswordValues,
} from "@/features/auth/schemas/auth-schemas";
import { describeError } from "@/lib/api/describe-error";
import { isApiError } from "@/lib/api/errors";
import { applyServerErrors } from "@/lib/forms/server-errors";
import { loginPath, routes } from "@/lib/navigation";

export function ResetPasswordForm() {
  const router = useRouter();
  const token = useSearchParams().get("token");
  const confirm = useConfirmPasswordReset();
  const [formError, setFormError] = useState<{ message: string; requestId: string } | null>(null);
  const [linkInvalid, setLinkInvalid] = useState(false);

  const form = useForm<ResetPasswordValues>({
    resolver: zodResolver(resetPasswordSchema),
    defaultValues: { new_password: "", confirm_password: "" },
  });
  const { errors } = form.formState;

  if (!token || linkInvalid) {
    return (
      <AuthCard
        title="This link can't be used"
        description="Reset links work once and expire after a short time. Request a new one to continue."
      >
        <Button asChild variant="primary" size="lg" className="w-full">
          <Link href={routes.forgotPassword}>Request a new link</Link>
        </Button>
      </AuthCard>
    );
  }

  async function onSubmit(values: ResetPasswordValues) {
    setFormError(null);
    try {
      await confirm.mutateAsync({ token: token as string, newPassword: values.new_password });
      // Resetting ends every session, including this browser's: send them to sign in.
      router.replace(loginPath({ reason: "reset" }));
    } catch (error) {
      if (
        isApiError(error) &&
        (error.status === 400 || error.status === 404 || error.status === 410)
      ) {
        // A used, expired, or unknown token is not fixable by retyping the password.
        setLinkInvalid(true);
        return;
      }
      setFormError(
        applyServerErrors(error, form.setError, ["new_password"]) ?? {
          message: describeError(error).message,
          requestId: "",
        },
      );
    }
  }

  return (
    <AuthCard title="Choose a new password" description="This signs you out everywhere else.">
      <form onSubmit={form.handleSubmit(onSubmit)} noValidate className="space-y-4">
        <FormError message={formError?.message} requestId={formError?.requestId} />

        <Field
          label="New password"
          description={`At least ${MIN_PASSWORD_LENGTH} characters.`}
          error={errors.new_password?.message}
        >
          {(control) => (
            <PasswordInput
              {...control}
              {...form.register("new_password")}
              autoComplete="new-password"
              autoFocus
            />
          )}
        </Field>

        <Field label="Confirm new password" error={errors.confirm_password?.message}>
          {(control) => (
            <PasswordInput
              {...control}
              {...form.register("confirm_password")}
              autoComplete="new-password"
            />
          )}
        </Field>

        <Button
          type="submit"
          variant="primary"
          size="lg"
          className="w-full"
          loading={form.formState.isSubmitting}
        >
          {form.formState.isSubmitting ? "Saving" : "Update password"}
        </Button>
      </form>
    </AuthCard>
  );
}
