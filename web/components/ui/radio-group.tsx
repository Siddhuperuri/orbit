"use client";

import * as RadioPrimitive from "@radix-ui/react-radio-group";
import type * as React from "react";

import { cn } from "@/lib/utils/cn";

export function RadioGroup({
  className,
  ...props
}: React.ComponentProps<typeof RadioPrimitive.Root>) {
  return <RadioPrimitive.Root className={cn("grid gap-2", className)} {...props} />;
}

export function RadioGroupItem({
  className,
  ...props
}: React.ComponentProps<typeof RadioPrimitive.Item>) {
  return (
    <RadioPrimitive.Item
      className={cn(
        "border-control bg-surface relative inline-flex size-4 shrink-0 items-center justify-center rounded-full border",
        "hover:border-fg-subtle transition-colors",
        "data-[state=checked]:border-accent-solid",
        "disabled:cursor-not-allowed disabled:opacity-50",
        "pointer-coarse:after:absolute pointer-coarse:after:-inset-3 pointer-coarse:after:content-['']",
        className,
      )}
      {...props}
    >
      <RadioPrimitive.Indicator className="bg-accent-solid size-2 rounded-full" />
    </RadioPrimitive.Item>
  );
}
