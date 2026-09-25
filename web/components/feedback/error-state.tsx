"use client";

import { AlertTriangle, WifiOff } from "lucide-react";

import { CopyButton } from "@/components/feedback/copy-button";
import { Button } from "@/components/ui/button";
import { describeError } from "@/lib/api/describe-error";
import { cn } from "@/lib/utils/cn";

/**
 * An error, stated the way the design brief requires: what failed, whether
 * retrying is worthwhile, and a reference support can search for.
 *
 * `role="alert"` makes a screen reader announce it the moment it appears, which
 * matters because an error replacing a spinner is otherwise a silent change.
 */
export function ErrorState({
  error,
  title,
  onRetry,
  retrying = false,
  compact = false,
  className,
}: {
  error: unknown;
  /** Overrides the generated title with something specific to the surface. */
  title?: string;
  onRetry?: () => void;
  retrying?: boolean;
  compact?: boolean;
  className?: string;
}) {
  const description = describeError(error);
  const Icon = description.kind === "offline" ? WifiOff : AlertTriangle;

  return (
    <div
      role="alert"
      className={cn(
        "flex gap-3",
        compact ? "py-3" : "mx-auto max-w-md flex-col items-center px-4 py-16 text-center",
        className,
      )}
    >
      <Icon
        className={cn("text-danger shrink-0", compact ? "mt-0.5 size-4" : "size-6")}
        aria-hidden="true"
      />
      <div className={cn("space-y-1.5", compact ? "min-w-0 flex-1 text-left" : "")}>
        <p className="text-fg text-base font-semibold">{title ?? description.title}</p>
        <p className="text-fg-muted text-base">{description.message}</p>
        {description.requestId ? (
          <p className="text-fg-subtle flex items-center gap-1 text-xs">
            <span>Reference</span>
            <code className="text-fg-muted font-mono">{description.requestId}</code>
            <CopyButton value={description.requestId} label="Copy reference" />
          </p>
        ) : null}
        {onRetry && description.retryable ? (
          <div className="pt-1.5">
            <Button size="sm" onClick={onRetry} loading={retrying}>
              {retrying ? "Retrying" : "Try again"}
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
