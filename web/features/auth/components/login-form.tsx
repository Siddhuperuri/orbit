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
import { Input } from "@/components/ui/input";
import { useLogin } from "@/features/auth/api/use-auth-mutations";
import { AuthCard } from "@/features/auth/components/auth-card";
import { SessionNotice } from "@/features/auth/components/session-notice";
import { loginSchema, type LoginValues } from "@/features/auth/schemas/auth-schemas";
import { describeError } from "@/lib/api/describe-error";
import { isApiError } from "@/lib/api/errors";
import { applyServerErrors } from "@/lib/forms/server-errors";
import { routes, safeNextPath } from "@/lib/navigation";

export function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const login = useLogin();
  const [formError, setFormError] = useState<{ message: string; requestId: string } | null>(null);

  const form = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  });
  const { errors } = form.formState;

  async function onSubmit(values: LoginValues) {
    setFormError(null);
    try {
      await login.mutateAsync(values);
      router.replace(safeNextPath(searchParams.get("next")));
    } catch (error) {
      if (isApiError(error) && error.code === "INVALID_CREDENTIALS") {
        // The same message whether the account exists or not -- that sameness is
        // the server's defence against account enumeration, so it is shown as is.
        setFormError({ message: error.message, requestId: error.requestId });
        form.resetField("password");
        form.setFocus("password");
        return;
      }
      setFormError(
        applyServerErrors(error, form.setError, ["email", "password"]) ?? {
          message: describeError(error).message,
          requestId: "",
        },
      );
    }
  }

  return (
    <AuthCard
      title="Sign in"
      description="Continue to your workspaces."
      footer={
        <>
          New to ORBIT?{" "}
          <Link
            href={routes.register}
            className="text-accent font-medium underline-offset-4 hover:underline"
          >
            Create an account
          </Link>
        </>
      }
    >
      <SessionNotice reason={searchParams.get("reason")} />

      {/* `method="post"`: a submit before React hydrates must not put the password
          in the address bar, history, and server logs, as a default GET would. */}
      <form method="post" onSubmit={form.handleSubmit(onSubmit)} noValidate className="space-y-4">
        <FormError message={formError?.message} requestId={formError?.requestId} />

        <Field label="Email" error={errors.email?.message}>
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

        <Field label="Password" error={errors.password?.message}>
          {(control) => (
            <PasswordInput
              {...control}
              {...form.register("password")}
              autoComplete="current-password"
            />
          )}
        </Field>

        <div className="flex items-center justify-between gap-3 pt-1">
          <Link
            href={routes.forgotPassword}
            className="text-accent text-base underline-offset-4 hover:underline"
          >
            Forgot password?
          </Link>
          <Button type="submit" variant="primary" size="lg" loading={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? "Signing in" : "Sign in"}
          </Button>
        </div>
      </form>
    </AuthCard>
  );
}
