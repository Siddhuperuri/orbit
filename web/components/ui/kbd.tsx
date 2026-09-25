import type * as React from "react";

import { cn } from "@/lib/utils/cn";

export function Kbd({ className, ...props }: React.ComponentProps<"kbd">) {
  return (
    <kbd
      className={cn(
        "border-line-strong inline-flex h-5 min-w-5 items-center justify-center rounded-xs border",
        "bg-surface text-fg-muted px-1 font-mono text-xs",
        className,
      )}
      {...props}
    />
  );
}
