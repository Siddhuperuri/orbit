import { AlertCircle } from "lucide-react";

import { cn } from "@/lib/utils/cn";

/**
 * A form-level error: something that is not about one field (a rejected login, a
 * network failure). `role="alert"` announces it when it appears; without it a
 * screen-reader user submits and hears nothing.
 */
export function FormError({
  message,
  requestId,
  className,
}: {
  message: string | null | undefined;
  requestId?: string;
  className?: string;
}) {
  if (!message) return null;
  return (
    <div
      role="alert"
      className={cn(
        "border-danger/30 bg-danger-soft text-danger flex items-start gap-2 rounded-md border px-3 py-2.5 text-base",
        className,
      )}
    >
      <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <div className="min-w-0">
        <p>{message}</p>
        {requestId ? (
          <p className="text-fg-muted mt-0.5 font-mono text-xs">Ref {requestId}</p>
        ) : null}
      </div>
    </div>
  );
}
