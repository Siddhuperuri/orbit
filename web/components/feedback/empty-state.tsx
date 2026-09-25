import type { ReactNode } from "react";

import { cn } from "@/lib/utils/cn";

/**
 * What to show when there is nothing yet. Per the design brief it explains what
 * belongs here and offers the action that fills it -- an empty state that just
 * says "No items" is a dead end.
 */
export function EmptyState({
  title,
  description,
  action,
  className,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "mx-auto flex max-w-md flex-col items-center px-4 py-16 text-center",
        className,
      )}
    >
      <h2 className="text-fg text-lg font-semibold">{title}</h2>
      {description ? <p className="text-fg-muted mt-1.5 text-base">{description}</p> : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}
