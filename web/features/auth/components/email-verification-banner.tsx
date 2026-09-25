"use client";

import { MailWarning, X } from "lucide-react";
import { useState } from "react";

import { notify } from "@/components/feedback/notify";
import { Button } from "@/components/ui/button";
import { useResendVerification } from "@/features/auth/api/use-auth-mutations";
import { useUser } from "@/features/auth/hooks/use-user";

/**
 * A nudge above the content while the account's email is unverified. It does not
 * block anything -- the backend does not gate features on verification -- and it
 * can be dismissed for the current visit; it returns next time until the address
 * is confirmed.
 */
export function EmailVerificationBanner() {
  const user = useUser();
  const resend = useResendVerification();
  const [dismissed, setDismissed] = useState(false);

  if (user.email_verified || dismissed) return null;

  return (
    <div
      role="region"
      aria-label="Email verification"
      className="border-warning/30 bg-warning-soft text-warning flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b px-4 py-2 text-sm"
    >
      <MailWarning className="size-4 shrink-0" aria-hidden="true" />
      <p className="min-w-0 flex-1 [overflow-wrap:anywhere]">
        <span className="sm:hidden">Confirm your email address to secure your account.</span>
        <span className="hidden sm:inline">
          Confirm <span className="font-medium">{user.email}</span> to secure your account and
          recover it if you forget your password.
        </span>
      </p>
      <Button
        size="sm"
        variant="secondary"
        loading={resend.isPending}
        onClick={() =>
          resend.mutate(undefined, {
            onSuccess: () =>
              notify.success("Verification email sent", { description: `Check ${user.email}.` }),
          })
        }
      >
        {resend.isPending ? "Sending" : "Resend link"}
      </Button>
      <Button size="icon" variant="ghost" aria-label="Dismiss" onClick={() => setDismissed(true)}>
        <X aria-hidden="true" />
      </Button>
    </div>
  );
}
