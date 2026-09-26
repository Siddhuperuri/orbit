import { cva, type VariantProps } from "class-variance-authority";
import type * as React from "react";

import { cn } from "@/lib/utils/cn";

/**
 * A compact label. State badges always carry an icon and text as well as a colour
 * (see `StatusBadge`), so meaning survives colour blindness and a monochrome
 * display.
 */
const badgeVariants = cva(
  cn(
    "inline-flex h-5.5 items-center gap-1.5 rounded-md border px-1.5 whitespace-nowrap",
    "font-mono text-2xs font-medium tracking-[0.06em] uppercase",
    "[&_svg]:size-3 [&_svg]:shrink-0",
  ),
  {
    variants: {
      // Outlined, like a catalogue tag: the tone is the rule and the ink, never a fill.
      tone: {
        neutral: "border-line-strong text-fg-muted",
        accent: "border-accent/60 text-accent",
        success: "border-success/60 text-success",
        warning: "border-warning/60 text-warning",
        danger: "border-danger/60 text-danger",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export interface BadgeProps
  extends React.ComponentProps<"span">, VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
