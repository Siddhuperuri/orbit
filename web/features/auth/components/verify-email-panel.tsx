"use client";

import { CheckCircle2, XCircle } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef } from "react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { useConfirmEmail } from "@/features/auth/api/use-auth-mutations";
import { AuthCard } from "@/features/auth/components/auth-card";
import { describeError } from "@/lib/api/describe-error";
import { routes } from "@/lib/navigation";

/**
 * Confirms an email address from the link in a message.
 *
 * The token is consumed the moment the page loads, which is why it is guarded: in
 * development React runs effects twice, and a second confirmation of a token the
 * first one already used would show a spurious failure.
 */
export function VerifyEmailPanel() {
  const token = useSearchParams().get("token");
  const confirm = useConfirmEmail();
  const started = useRef(false);
  const { mutate } = confirm;

  useEffect(() => {
    if (!token || started.current) return;
    started.current = true;
    mutate(token);
  }, [token, mutate]);

  if (!token) {
    return (
      <AuthCard title="This link can't be used" description="It's missing its verification code.">
        <Button asChild size="lg" className="w-full">
          <Link href={routes.home}>Continue to ORBIT</Link>
        </Button>
      </AuthCard>
    );
  }

  if (confirm.isSuccess) {
    return (
      <AuthCard title="Email confirmed" description="Your address is verified. Thanks.">
        <p role="status" className="text-success flex items-center gap-2 text-base">
          <CheckCircle2 className="size-4" aria-hidden="true" />
          Verified
        </p>
        <Button asChild variant="primary" size="lg" className="w-full">
          <Link href={routes.home}>Continue to ORBIT</Link>
        </Button>
      </AuthCard>
    );
  }

  if (confirm.isError) {
    const description = describeError(confirm.error);
    return (
      <AuthCard
        title="We couldn't verify that link"
        description="It may have expired or already been used. Sign in and request a fresh one from the banner at the top of the app."
      >
        <p role="alert" className="text-danger flex items-start gap-2 text-base">
          <XCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          {description.message}
        </p>
        <Button asChild variant="primary" size="lg" className="w-full">
          <Link href={routes.home}>Continue to ORBIT</Link>
        </Button>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Confirming your email">
      <p role="status" className="text-fg-muted flex items-center gap-2 text-base">
        <Spinner />
        Checking your link…
      </p>
    </AuthCard>
  );
}
