"use client";

import * as MenuPrimitive from "@radix-ui/react-dropdown-menu";
import { Check } from "lucide-react";
import type * as React from "react";

import { cn } from "@/lib/utils/cn";

/**
 * Menus on Radix: arrow-key navigation, typeahead, `Home`/`End`, `Escape`, and
 * roving focus are all provided, along with the correct `menu`/`menuitem` roles.
 */
export const DropdownMenu = MenuPrimitive.Root;
export const DropdownMenuTrigger = MenuPrimitive.Trigger;
export const DropdownMenuGroup = MenuPrimitive.Group;
export const DropdownMenuRadioGroup = MenuPrimitive.RadioGroup;

const itemClasses = cn(
  "relative flex min-h-9 cursor-default items-center gap-3 px-2.5 py-1.5 text-base text-fg outline-none select-none transition-colors duration-200",
  "data-[highlighted]:bg-fill",
  "data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
  "[&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:text-fg-subtle data-[highlighted]:[&_svg]:text-fg",
  "pointer-coarse:min-h-11",
);

export function DropdownMenuContent({
  className,
  sideOffset = 6,
  ...props
}: React.ComponentProps<typeof MenuPrimitive.Content>) {
  return (
    <MenuPrimitive.Portal>
      <MenuPrimitive.Content
        sideOffset={sideOffset}
        collisionPadding={8}
        className={cn(
          "bg-surface border-line shadow-float z-50 min-w-48 overflow-hidden border p-1",
          "data-[state=open]:animate-pop-in data-[state=closed]:animate-pop-out",
          className,
        )}
        {...props}
      />
    </MenuPrimitive.Portal>
  );
}

export function DropdownMenuItem({
  className,
  destructive = false,
  ...props
}: React.ComponentProps<typeof MenuPrimitive.Item> & { destructive?: boolean }) {
  return (
    <MenuPrimitive.Item
      className={cn(
        itemClasses,
        destructive &&
          "text-danger data-[highlighted]:bg-danger-soft [&_svg]:text-danger data-[highlighted]:[&_svg]:text-danger",
        className,
      )}
      {...props}
    />
  );
}

export function DropdownMenuRadioItem({
  className,
  children,
  ...props
}: React.ComponentProps<typeof MenuPrimitive.RadioItem>) {
  return (
    <MenuPrimitive.RadioItem className={cn(itemClasses, "pr-8", className)} {...props}>
      {children}
      <MenuPrimitive.ItemIndicator className="[&_svg]:text-accent absolute right-2 inline-flex">
        <Check aria-hidden="true" />
      </MenuPrimitive.ItemIndicator>
    </MenuPrimitive.RadioItem>
  );
}

export function DropdownMenuLabel({
  className,
  ...props
}: React.ComponentProps<typeof MenuPrimitive.Label>) {
  return (
    <MenuPrimitive.Label
      className={cn("label-micro text-fg-subtle px-2.5 pt-2.5 pb-1.5", className)}
      {...props}
    />
  );
}

export function DropdownMenuSeparator({
  className,
  ...props
}: React.ComponentProps<typeof MenuPrimitive.Separator>) {
  return (
    <MenuPrimitive.Separator className={cn("bg-line -mx-1 my-1 h-px", className)} {...props} />
  );
}
