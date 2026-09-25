"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { FormError } from "@/components/forms/form-error";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { useRequestPasswordReset } from "@/features/auth/api/use-auth-mutations";
import { AuthCard } from "@/features/auth/components/auth-card";
import {
  forgotPasswordSchema,
  type ForgotPasswordValues,
} from "@/features/auth/schemas/auth-schemas";
import { describeError } from "@/lib/api/describe-error";
import { isApiError } from "@/lib/api/errors";
import { routes } from "@/lib/navigation";

export function ForgotPasswordForm() {
  const request = useRequestPasswordReset();
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [formError, setFormError] = useState<{ message: string; requestId: string } | null>(null);

  const form = useForm<ForgotPasswordValues>({
    resolver: zodResolver(forgotPasswordSchema),
    defaultValues: { email: "" },
  });

  async function onSubmit({ email }: ForgotPasswordValues) {
    setFormError(null);
    try {
      await request.mutateAsync(email);
      setSentTo(email);
    } catch (error) {
      const description = describeError(error);
      setFormError({
        message: description.message,
        requestId: isApiError(error) ? error.requestId : "",
      });
    }
  }

  if (sentTo) {
    return (
      <AuthCard
        title="Check your email"
        description={
          <>
            If an account exists for <strong className="text-fg font-medium">{sentTo}</strong>,
            we&apos;ve sent a link to choose a new password. It expires soon, so use it promptly.
          </>
        }
        footer={
          <Link
            href={routes.login}
            className="text-accent font-medium underline-offset-4 hover:underline"
          >
            Back to sign in
          </Link>
        }
      >
        <p className="text-fg-muted text-base">
          Nothing arrived? Check spam, or{" "}
          <button
            type="button"
            onClick={() => setSentTo(null)}
            className="text-accent rounded-xs font-medium underline-offset-4 hover:underline"
          >
            try a different address
          </button>
          .
        </p>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title="Reset your password"
      description="Enter your email and we'll send a link to choose a new one."
      footer={
        <Link
          href={routes.login}
          className="text-accent font-medium underline-offset-4 hover:underline"
        >
          Back to sign in
        </Link>
      }
    >
      <form onSubmit={form.handleSubmit(onSubmit)} noValidate className="space-y-4">
        <FormError message={formError?.message} requestId={formError?.requestId} />

        <Field label="Email" error={form.formState.errors.email?.message}>
          {(control) => (
            <Input
              {...control}
              {...form.register("email")}
              type="email"
              autoComplete="email"
              inputMode="email"
              autoCapitalize="off"
              spellCheck={false}
              autoFocus
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
          {form.formState.isSubmitting ? "Sending" : "Send reset link"}
        </Button>
      </form>
    </AuthCard>
  );
}
