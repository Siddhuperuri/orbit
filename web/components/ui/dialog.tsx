"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type * as React from "react";

import { cn } from "@/lib/utils/cn";

/**
 * Modal dialog on Radix: focus is trapped while open and restored to the trigger
 * on close, `Escape` closes it, and everything behind it is inert. Those are the
 * behaviours hand-rolled dialogs most often get wrong, which is why this is a
 * primitive and not markup.
 *
 * `DialogTitle` is required by the component contract: an untitled dialog is
 * announced by a screen reader as an unnamed region.
 */
export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;
export const DialogPortal = DialogPrimitive.Portal;

export function DialogOverlay({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Overlay>) {
  return (
    <DialogPrimitive.Overlay
      className={cn(
        "bg-overlay fixed inset-0 z-50",
        "data-[state=open]:animate-fade-in data-[state=closed]:animate-fade-out",
        className,
      )}
      {...props}
    />
  );
}

/**
 * Where focus goes if, once the dialog closes, it has nowhere sensible to land.
 *
 * Radix returns focus to whatever had it when the dialog opened. When a dialog is
 * opened from a menu item that is the wrong element to remember: the menu closes and
 * unmounts, so focus falls to `<body>` and a keyboard user restarts at the top of the
 * page. After Radix has tried, this checks whether focus was lost and, if so, sends it
 * to `returnFocusRef` (the control that owns the menu) or, failing that, to the page's
 * `<main>`.
 */
function restoreLostFocus(returnFocusRef: React.RefObject<HTMLElement | null> | undefined) {
  setTimeout(() => {
    const active = document.activeElement;
    if (active && active !== document.body) return;
    (returnFocusRef?.current ?? document.getElementById("main"))?.focus({ preventScroll: true });
  }, 0);
}

export function DialogContent({
  className,
  children,
  showClose = true,
  returnFocusRef,
  onCloseAutoFocus,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Content> & {
  showClose?: boolean;
  /** The control to refocus on close when the dialog was opened from a menu item. */
  returnFocusRef?: React.RefObject<HTMLElement | null>;
}) {
  return (
    <DialogPortal>
      <DialogOverlay />
      <DialogPrimitive.Content
        className={cn(
          "fixed top-1/2 left-1/2 z-50 w-[calc(100vw-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2",
          "border-line bg-surface shadow-float max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-lg border p-5",
          "data-[state=open]:animate-pop-in data-[state=closed]:animate-pop-out",
          className,
        )}
        onCloseAutoFocus={(event) => {
          onCloseAutoFocus?.(event);
          if (!event.defaultPrevented) restoreLostFocus(returnFocusRef);
        }}
        {...props}
      >
        {children}
        {showClose ? (
          <DialogPrimitive.Close
            className="text-fg-muted hover:bg-sunken hover:text-fg absolute top-3 right-3 inline-flex size-7 items-center justify-center rounded-md pointer-coarse:size-11"
            aria-label="Close"
          >
            <X className="size-4" aria-hidden="true" />
          </DialogPrimitive.Close>
        ) : null}
      </DialogPrimitive.Content>
    </DialogPortal>
  );
}

export function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("mb-4 space-y-1.5 pr-8", className)} {...props} />;
}

export function DialogTitle({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Title>) {
  return (
    <DialogPrimitive.Title
      className={cn("text-fg text-lg leading-snug font-semibold", className)}
      {...props}
    />
  );
}

export function DialogDescription({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Description>) {
  return (
    <DialogPrimitive.Description className={cn("text-fg-muted text-base", className)} {...props} />
  );
}

export function DialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end", className)}
      {...props}
    />
  );
}
