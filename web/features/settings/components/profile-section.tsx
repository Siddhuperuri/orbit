"use client";

import { CheckCircle2, Clock } from "lucide-react";

import { CopyButton } from "@/components/feedback/copy-button";
import { notify } from "@/components/feedback/notify";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useResendVerification } from "@/features/auth/api/use-auth-mutations";
import { useUser } from "@/features/auth/hooks/use-user";
import { formatDate } from "@/lib/utils/format";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-x-6 gap-y-1 py-3 sm:grid-cols-[9rem_1fr]">
      <dt className="text-fg-muted text-sm font-medium">{label}</dt>
      <dd className="text-fg min-w-0 text-base">{children}</dd>
    </div>
  );
}

/**
 * Who is signed in. Read-only: the API offers no way to change a name or address
 * yet, and a form that pretends otherwise would be worse than none.
 */
export function ProfileSection() {
  const user = useUser();
  const resend = useResendVerification();

  return (
    <dl className="divide-line -my-3 divide-y">
      <Row label="Name">{user.full_name}</Row>
      <Row label="Email">
        <div className="flex flex-wrap items-center gap-2">
          <span className="[overflow-wrap:anywhere]">{user.email}</span>
          {user.email_verified ? (
            <Badge tone="success">
              <CheckCircle2 aria-hidden="true" />
              Verified
            </Badge>
          ) : (
            <>
              <Badge tone="warning">
                <Clock aria-hidden="true" />
                Not verified
              </Badge>
              <Button
                size="xs"
                loading={resend.isPending}
                onClick={() =>
                  resend.mutate(undefined, {
                    onSuccess: () =>
                      notify.success("Verification email sent", {
                        description: `Check ${user.email}.`,
                      }),
                  })
                }
              >
                {resend.isPending ? "Sending" : "Resend link"}
              </Button>
            </>
          )}
        </div>
      </Row>
      <Row label="Member since">{formatDate(user.created_at)}</Row>
      <Row label="Account ID">
        <div className="flex items-center gap-1">
          <code className="font-mono text-sm break-all">{user.id}</code>
          <CopyButton value={user.id} label="Copy account ID" />
        </div>
        <p className="text-fg-muted mt-1 text-sm">
          A workspace admin needs this to add you to a workspace.
        </p>
      </Row>
    </dl>
  );
}
