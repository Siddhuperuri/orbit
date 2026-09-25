import type * as React from "react";

import { cn } from "@/lib/utils/cn";

/**
 * Text-like controls share one visual definition. Font size is 16px on small
 * screens: iOS Safari zooms the viewport into any focused field below that,
 * which is disorienting and cannot be turned off without disabling zoom (which
 * WCAG 1.4.4 forbids).
 */
export const controlClasses = cn(
  "w-full rounded-md border border-control bg-surface px-2.5 text-md text-fg sm:text-base",
  "placeholder:text-fg-subtle",
  "transition-colors duration-150",
  "hover:border-fg-subtle",
  "aria-invalid:border-danger aria-invalid:hover:border-danger",
  "disabled:cursor-not-allowed disabled:bg-sunken disabled:text-fg-subtle disabled:hover:border-control",
  "read-only:bg-sunken",
);

export function Input({ className, type = "text", ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      className={cn(controlClasses, "h-8 pointer-coarse:h-11", className)}
      {...props}
    />
  );
}

export function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return <textarea className={cn(controlClasses, "min-h-20 py-2", className)} {...props} />;
}

/**
 * A native `<select>`: the browser's own picker is the most accessible option and
 * the best on touch, and a custom listbox would have to re-earn both.
 */
export function NativeSelect({ className, children, ...props }: React.ComponentProps<"select">) {
  return (
    <select className={cn(controlClasses, "h-8 pr-8 pointer-coarse:h-11", className)} {...props}>
      {children}
    </select>
  );
}
