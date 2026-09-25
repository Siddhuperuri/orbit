import { Info } from "lucide-react";

const MESSAGES: Record<string, string> = {
  expired: "Your session expired. Sign in to continue where you left off.",
  "signed-out": "You've been signed out.",
  reset: "Your password was updated. Sign in with the new one.",
  registered: "Your account is ready. Sign in to continue.",
};

/** Explains why the user is looking at the sign-in screen, when it is not obvious. */
export function SessionNotice({ reason }: { reason: string | null }) {
  const message = reason ? MESSAGES[reason] : undefined;
  if (!message) return null;
  return (
    <p
      role="status"
      className="border-line bg-sunken text-fg-muted flex items-start gap-2 rounded-md border px-3 py-2.5 text-base"
    >
      <Info className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      {message}
    </p>
  );
}
