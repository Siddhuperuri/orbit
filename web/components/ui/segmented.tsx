"use client";

import * as RadioPrimitive from "@radix-ui/react-radio-group";
import type * as React from "react";

import { cn } from "@/lib/utils/cn";

/**
 * A small set of mutually exclusive options shown side by side, like a toggle
 * with more than two positions. It is a radio group underneath (Radix), so it is
 * one tab stop, arrow keys move the selection, and a screen reader announces
 * "radio button, 2 of 3, checked".
 */
export function Segmented({
  className,
  ...props
}: React.ComponentProps<typeof RadioPrimitive.Root>) {
  return (
    <RadioPrimitive.Root
      orientation="horizontal"
      className={cn("border-line inline-flex h-9 items-stretch border", className)}
      {...props}
    />
  );
}

export function SegmentedItem({
  className,
  ...props
}: React.ComponentProps<typeof RadioPrimitive.Item>) {
  return (
    <RadioPrimitive.Item
      className={cn(
        "label-caps text-fg-muted border-line inline-flex items-center justify-center gap-1.5 px-4 whitespace-nowrap transition-colors duration-300 not-first:border-l",
        "hover:bg-fill hover:text-fg",
        "data-[state=checked]:bg-fg data-[state=checked]:text-canvas",
        "disabled:pointer-events-none disabled:opacity-50",
        "pointer-coarse:min-h-11",
        "[&_svg]:size-3.5 [&_svg]:shrink-0",
        className,
      )}
      {...props}
    />
  );
}
