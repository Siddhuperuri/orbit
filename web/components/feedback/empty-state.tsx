import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils/cn";

/**
 * The icon of an empty or first-run state, set in the product's orbit motif: a
 * tile inside two faint rings. Decorative.
 */
export function OrbitIcon({ icon: Icon, className }: { icon: LucideIcon; className?: string }) {
  return (
    <span aria-hidden="true" className={cn("relative inline-flex size-24", className)}>
      <span className="border-line absolute inset-0 rounded-full border" />
      {/* A slow satellite on the outer orbit. */}
      <span className="animate-orbit-spin absolute inset-0">
        <span className="bg-accent absolute top-1/2 -right-1 size-2 -translate-y-1/2 rounded-full" />
      </span>
      <span className="border-line-strong absolute inset-3 rounded-full border border-dashed" />
      <span className="bg-canvas border-line-strong text-fg absolute inset-7 inline-flex items-center justify-center border">
        <Icon className="size-5" strokeWidth={1.5} />
      </span>
    </span>
  );
}

/**
 * What to show when there is nothing yet. Per the design brief it explains what
 * belongs here and offers the action that fills it -- an empty state that just
 * says "No items" is a dead end.
 */
export function EmptyState({
  title,
  description,
  action,
  icon,
  className,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  icon?: LucideIcon;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "mx-auto flex max-w-md flex-col items-center px-4 py-16 text-center",
        className,
      )}
    >
      {icon ? <OrbitIcon icon={icon} className="enter mb-7" /> : null}
      <h2 className="enter enter-1 text-fg text-3xl font-semibold tracking-tight">{title}</h2>
      {description ? (
        <p className="enter enter-2 text-fg-muted mt-3 text-base">{description}</p>
      ) : null}
      {action ? <div className="enter enter-3 mt-7">{action}</div> : null}
    </div>
  );
}
