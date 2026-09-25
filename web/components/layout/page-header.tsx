import type { ReactNode } from "react";

import { cn } from "@/lib/utils/cn";

/**
 * The top of a page: its one `<h1>`, an optional line of context, and the page's
 * primary actions. The `<h1>` is the landmark a screen-reader user navigates by,
 * so every page has exactly one.
 *
 * `serif` is for a page whose title is the *user's own text* -- a document's name
 * -- following the design system's rule that serif means content.
 *
 * `titleAction` sits beneath the heading rather than inside it: a control placed
 * within the `<h1>` becomes part of the heading's accessible name ("Budget review
 * Rename"), which is what a screen-reader user hears when they navigate by heading.
 */
export function PageHeader({
  title,
  titleAction,
  description,
  actions,
  serif = false,
  className,
}: {
  title: ReactNode;
  titleAction?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  serif?: boolean;
  className?: string;
}) {
  return (
    <header
      className={cn("mb-6 flex flex-wrap items-start justify-between gap-x-4 gap-y-3", className)}
    >
      <div className="min-w-0 flex-1">
        <h1
          className={cn(
            "text-fg text-xl leading-snug font-semibold [overflow-wrap:anywhere]",
            serif && "font-serif text-xl font-medium sm:text-2xl",
          )}
        >
          {title}
        </h1>
        {titleAction ? <div className="mt-1">{titleAction}</div> : null}
        {description ? <p className="text-fg-muted mt-1 text-base">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}
