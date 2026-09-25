"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { FormError } from "@/components/forms/form-error";
import { PasswordInput } from "@/components/forms/password-input";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { useLogin, useRegister } from "@/features/auth/api/use-auth-mutations";
import { AuthCard } from "@/features/auth/components/auth-card";
import {
  MIN_PASSWORD_LENGTH,
  registerSchema,
  type RegisterValues,
} from "@/features/auth/schemas/auth-schemas";
import { describeError } from "@/lib/api/describe-error";
import { ErrorCode, isApiError } from "@/lib/api/errors";
import { applyServerErrors } from "@/lib/forms/server-errors";
import { loginPath, routes } from "@/lib/navigation";

export function RegisterForm() {
  const router = useRouter();
  const register = useRegister();
  const login = useLogin();
  const [formError, setFormError] = useState<{ message: string; requestId: string } | null>(null);

  const form = useForm<RegisterValues>({
    resolver: zodResolver(registerSchema),
    defaultValues: { full_name: "", email: "", password: "" },
  });
  const { errors } = form.formState;

  async function onSubmit(values: RegisterValues) {
    setFormError(null);

    try {
      await register.mutateAsync(values);
    } catch (error) {
      if (isApiError(error) && error.code === ErrorCode.Conflict) {
        form.setError("email", { type: "server", message: error.message });
        form.setFocus("email");
        return;
      }
      setFormError(
        applyServerErrors(error, form.setError, ["full_name", "email", "password"]) ?? {
          message: describeError(error).message,
          requestId: "",
        },
      );
      return;
    }

    // The API's registration does not start a session, so signing in is a second
    // call. Doing it here saves the person retyping what they just entered.
    try {
      await login.mutateAsync({ email: values.email, password: values.password });
      router.replace(routes.home);
    } catch {
      // The account exists; only the convenience failed. Send them to sign in
      // rather than showing an error for something that succeeded.
      router.replace(loginPath({ reason: "registered" }));
    }
  }

  return (
    <AuthCard
      title="Create your account"
      description="You'll create your first workspace next."
      footer={
        <>
          Already have an account?{" "}
          <Link
            href={routes.login}
            className="text-accent font-medium underline-offset-4 hover:underline"
          >
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={form.handleSubmit(onSubmit)} noValidate className="space-y-4">
        <FormError message={formError?.message} requestId={formError?.requestId} />

        <Field label="Full name" error={errors.full_name?.message}>
          {(control) => (
            <Input {...control} {...form.register("full_name")} autoComplete="name" autoFocus />
          )}
        </Field>

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
            />
          )}
        </Field>

        <Field
          label="Password"
          description={`At least ${MIN_PASSWORD_LENGTH} characters. A passphrase works well.`}
          error={errors.password?.message}
        >
          {(control) => (
            <PasswordInput
              {...control}
              {...form.register("password")}
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
          {form.formState.isSubmitting ? "Creating account" : "Create account"}
        </Button>
      </form>
    </AuthCard>
  );
}
