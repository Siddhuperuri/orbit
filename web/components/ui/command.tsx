"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Command as CommandPrimitive } from "cmdk";
import { Search } from "lucide-react";
import type * as React from "react";

import { DialogOverlay, DialogPortal, Dialog } from "@/components/ui/dialog";
import { Kbd } from "@/components/ui/kbd";
import { cn } from "@/lib/utils/cn";

/**
 * A command palette: a modal combobox. `cmdk` supplies the listbox semantics
 * (`role="combobox"` input controlling a `role="listbox"`, `aria-activedescendant`
 * for the highlighted option, arrow-key and `Enter` handling); the surrounding
 * dialog supplies the focus trap and `Escape`.
 *
 * Composed from the dialog primitives instead of cmdk's own `Command.Dialog`,
 * because that one renders an untitled dialog and Radix rightly warns that an
 * unnamed modal is announced as nothing.
 */
export function CommandDialog({
  open,
  onOpenChange,
  title,
  description,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogPortal>
        <DialogOverlay />
        <DialogPrimitive.Content
          className={cn(
            "fixed top-[12dvh] left-1/2 z-50 w-[calc(100vw-1.5rem)] max-w-xl -translate-x-1/2",
            "bg-surface border-line shadow-float overflow-hidden border",
            "data-[state=open]:animate-pop-in data-[state=closed]:animate-pop-out",
          )}
        >
          <DialogPrimitive.Title className="sr-only">{title}</DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">
            {description}
          </DialogPrimitive.Description>
          <CommandPrimitive label={title} loop>
            {children}
          </CommandPrimitive>
          {/* The keys, for anyone who reached the palette by mouse. Decorative: the
              dialog's description already says the same to a screen reader. */}
          <div
            aria-hidden="true"
            className="border-line text-fg-subtle label-micro hidden items-center gap-5 border-t px-4 py-3 sm:flex"
          >
            <span className="flex items-center gap-1.5">
              <Kbd>↑</Kbd>
              <Kbd>↓</Kbd>
              to move
            </span>
            <span className="flex items-center gap-1.5">
              <Kbd>↵</Kbd>
              to open
            </span>
            <span className="flex items-center gap-1.5">
              <Kbd>esc</Kbd>
              to close
            </span>
          </div>
        </DialogPrimitive.Content>
      </DialogPortal>
    </Dialog>
  );
}

export function CommandInput({
  className,
  ...props
}: React.ComponentProps<typeof CommandPrimitive.Input>) {
  return (
    <div className="border-line flex items-center gap-3 border-b px-4">
      <Search className="text-fg-subtle size-4.5 shrink-0" aria-hidden="true" />
      <CommandPrimitive.Input
        className={cn(
          "text-fg placeholder:text-fg-subtle h-16 w-full bg-transparent font-serif text-xl tracking-tight",
          // The palette's own focus ring would sit inside a bordered dialog; the
          // dialog itself is the visible focus container.
          "outline-none",
          className,
        )}
        {...props}
      />
    </div>
  );
}

export function CommandList({
  className,
  ...props
}: React.ComponentProps<typeof CommandPrimitive.List>) {
  return (
    <CommandPrimitive.List
      className={cn("max-h-[min(26rem,60dvh)] overflow-y-auto p-2", className)}
      {...props}
    />
  );
}

export function CommandEmpty(props: React.ComponentProps<typeof CommandPrimitive.Empty>) {
  return (
    <CommandPrimitive.Empty className="text-fg-muted px-3 py-8 text-center text-base" {...props} />
  );
}

export function CommandGroup({
  className,
  ...props
}: React.ComponentProps<typeof CommandPrimitive.Group>) {
  return (
    <CommandPrimitive.Group
      className={cn(
        "[&_[cmdk-group-heading]]:text-2xs [&_[cmdk-group-heading]]:px-2.5 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:pb-1.5 [&_[cmdk-group-heading]]:font-mono",
        "[&_[cmdk-group-heading]]:text-fg-subtle [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:tracking-[0.09em] [&_[cmdk-group-heading]]:uppercase",
        className,
      )}
      {...props}
    />
  );
}

export function CommandItem({
  className,
  ...props
}: React.ComponentProps<typeof CommandPrimitive.Item>) {
  return (
    <CommandPrimitive.Item
      className={cn(
        "text-fg flex min-h-10 cursor-default items-center gap-3 px-2.5 py-1.5 text-base select-none",
        "data-[selected=true]:bg-fg data-[selected=true]:text-canvas",
        "data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50",
        "[&_svg]:text-fg-subtle data-[selected=true]:[&_svg]:text-canvas [&_svg]:size-4 [&_svg]:shrink-0",
        "pointer-coarse:min-h-11",
        className,
      )}
      {...props}
    />
  );
}

/*
 * There is deliberately no `CommandSeparator`. cmdk renders it as `role="separator"`
 * *inside* the `role="listbox"`, and ARIA permits only options and groups there --
 * axe reports it as a critical `aria-required-children` failure. Group headings
 * already divide the list.
 */
