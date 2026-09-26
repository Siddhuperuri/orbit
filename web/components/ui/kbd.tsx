import type * as React from "react";

import { cn } from "@/lib/utils/cn";

export function Kbd({ className, ...props }: React.ComponentProps<"kbd">) {
  return (
    <kbd
      className={cn(
        "border-line-strong inline-flex h-5 min-w-5 items-center justify-center rounded-md border",
        "text-fg-subtle text-2xs px-1.5 font-mono font-medium tracking-wide",
        className,
      )}
      {...props}
    />
  );
}
