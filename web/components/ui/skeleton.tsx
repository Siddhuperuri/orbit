import type * as React from "react";

import { cn } from "@/lib/utils/cn";

/**
 * A placeholder shaped like the content it stands in for, so nothing shifts when
 * the real content arrives. Hidden from assistive tech: the *region* announces
 * that it is loading (see `LoadingRegion`), not each block within it.
 */
export function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      aria-hidden="true"
      className={cn("animate-skeleton bg-line rounded-sm", className)}
      {...props}
    />
  );
}

/** Wraps skeletons so a screen reader hears one "Loading" rather than silence. */
export function LoadingRegion({
  label = "Loading",
  className,
  children,
}: {
  label?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div role="status" aria-busy="true" className={className}>
      <span className="sr-only">{label}…</span>
      {children}
    </div>
  );
}
