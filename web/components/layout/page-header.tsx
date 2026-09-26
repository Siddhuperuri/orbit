import type { ReactNode } from "react";

import { KineticText } from "@/components/ui/kinetic-text";
import { cn } from "@/lib/utils/cn";

/** Past this many characters a title is the user's own long name, set a step smaller. */
const LONG_TITLE = 30;
/** Up to this many characters a description is a fact, not a sentence. */
const SHORT_FACT = 40;

/**
 * The top of a page, set like a film's title card: an oversized serif heading that
 * rises into place word by word, a small mono label above it, and the page's
 * context and actions on a line of their own beneath. As the page scrolls, the card
 * drifts up and dims (`.scroll-drift`) -- the title leaving the screen.
 *
 * The `<h1>` is the landmark a screen-reader user navigates by, so every page has
 * exactly one, and its accessible name is exactly its text.
 *
 * `titleAction` sits beneath the heading rather than inside it: a control placed
 * within the `<h1>` becomes part of the heading's accessible name ("Budget review
 * Rename"), which is what a screen-reader user hears when they navigate by heading.
 */
export function PageHeader({
  title,
  titleAction,
  eyebrow,
  description,
  actions,
  className,
}: {
  title: ReactNode;
  titleAction?: ReactNode;
  /** A short label above the title: the kind of page, or where it sits. */
  eyebrow?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  const long = typeof title === "string" && title.length > LONG_TITLE;

  return (
    <header className={cn("page-header mb-12 xl:mb-16", className)}>
      <div className="scroll-drift min-w-0">
        {eyebrow ? (
          <div className="enter label-micro text-fg-subtle mb-5 flex items-center gap-2">
            {eyebrow}
          </div>
        ) : null}
        <h1
          className={cn(
            "text-fg text-balance [overflow-wrap:anywhere]",
            long ? "display-title-sm" : "display-title",
          )}
        >
          {typeof title === "string" ? <KineticText text={title} /> : title}
        </h1>
      </div>

      {titleAction || description || actions ? (
        <div className="border-line mt-8 flex flex-wrap items-end justify-between gap-x-6 gap-y-4 border-t pt-4">
          <div className="enter enter-2 min-w-0 grow basis-64">
            {titleAction ? <div className="-ml-1">{titleAction}</div> : null}
            {description ? (
              <p
                className={cn(
                  "text-fg-muted mt-1 max-w-2xl",
                  // A short fact ("11 documents") is set as a label; a sentence stays
                  // in sentence case, where it is easier to read.
                  typeof description === "string" && description.length <= SHORT_FACT
                    ? "label-micro"
                    : "text-base",
                )}
              >
                {description}
              </p>
            ) : null}
          </div>
          {actions ? (
            <div className="enter enter-3 flex shrink-0 flex-wrap items-center gap-2">
              {actions}
            </div>
          ) : null}
        </div>
      ) : null}
    </header>
  );
}
