"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type * as React from "react";

import { DialogOverlay, DialogPortal } from "@/components/ui/dialog";
import { cn } from "@/lib/utils/cn";

/**
 * An edge-anchored panel, used for navigation on screens too narrow for a
 * persistent sidebar. It is a dialog underneath, so it has the same focus trap,
 * focus restoration, and `Escape` behaviour -- a drawer that leaves focus
 * wandering behind it is a common and serious keyboard failure.
 */
export const Sheet = DialogPrimitive.Root;
export const SheetTrigger = DialogPrimitive.Trigger;
export const SheetClose = DialogPrimitive.Close;
export const SheetTitle = DialogPrimitive.Title;
export const SheetDescription = DialogPrimitive.Description;

export function SheetContent({
  className,
  children,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Content>) {
  return (
    <DialogPortal>
      <DialogOverlay />
      <DialogPrimitive.Content
        className={cn(
          "border-line bg-sunken shadow-float fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw] flex-col border-r",
          "data-[state=open]:animate-slide-in-left data-[state=closed]:animate-slide-out-left",
          className,
        )}
        {...props}
      >
        {children}
        <DialogPrimitive.Close
          className="text-fg-muted hover:bg-line hover:text-fg absolute top-2.5 right-2.5 inline-flex size-8 items-center justify-center rounded-md pointer-coarse:size-11"
          aria-label="Close navigation"
        >
          <X className="size-4" aria-hidden="true" />
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPortal>
  );
}
