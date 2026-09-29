"use client";

import * as CheckboxPrimitive from "@radix-ui/react-checkbox";
import { Check } from "lucide-react";
import type * as React from "react";

import { cn } from "@/lib/utils/cn";

export function Checkbox({
  className,
  ...props
}: React.ComponentProps<typeof CheckboxPrimitive.Root>) {
  return (
    <CheckboxPrimitive.Root
      className={cn(
        "peer border-control bg-surface text-on-accent inline-flex size-4 shrink-0 items-center justify-center border",
        "hover:border-fg-subtle transition-colors",
        "data-[state=checked]:border-accent-solid data-[state=checked]:bg-accent-solid",
        "disabled:cursor-not-allowed disabled:opacity-50",
        // A 16px box is a small target for a finger; extend the hit area
        // invisibly on coarse pointers rather than enlarging the visual.
        "relative pointer-coarse:after:absolute pointer-coarse:after:-inset-3 pointer-coarse:after:content-['']",
        className,
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator>
        <Check className="size-3" strokeWidth={3} aria-hidden="true" />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}
