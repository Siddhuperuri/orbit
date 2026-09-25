"use client";

import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import type * as React from "react";

import { cn } from "@/lib/utils/cn";

export const TooltipProvider = TooltipPrimitive.Provider;

/**
 * A short label for a control that has no visible text (an icon button).
 *
 * A tooltip is a *supplement*, never the only accessible name: every trigger
 * still needs its own `aria-label`, because tooltips do not appear on touch and
 * are not part of the accessible-name computation for the trigger.
 */
export function Tip({
  label,
  children,
  side = "bottom",
  className,
}: {
  label: React.ReactNode;
  children: React.ReactElement;
  side?: "top" | "right" | "bottom" | "left";
  className?: string;
}) {
  return (
    <TooltipPrimitive.Root>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          sideOffset={6}
          className={cn(
            "bg-fg text-canvas shadow-float z-50 max-w-64 rounded-sm px-2 py-1 text-xs",
            "data-[state=delayed-open]:animate-fade-in",
            className,
          )}
        >
          {label}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}
