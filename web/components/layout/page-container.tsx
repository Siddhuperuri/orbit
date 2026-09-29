import type { ReactNode } from "react";

import { cn } from "@/lib/utils/cn";

/**
 * The frame every page is composed in. It shares its horizontal inset with the
 * column rules (`GRID_INSET`), so the page title can run the full width while the
 * body sits on chosen columns of the six-column desktop grid:
 *
 *   wide     all six columns (dense tables)
 *   default  columns 1-5, leaving the sixth empty -- the asymmetric default
 *   narrow   columns 2-5, for reading and forms
 *   full     no frame at all (chat manages its own layout)
 *
 * The spans are applied in CSS (`[data-width]` in `globals.css`) to everything but
 * the page header, and only on desktop widths; smaller screens use the full width.
 * It is also a size container, so display type is sized from it, not the window.
 */
export function PageContainer({
  width = "default",
  className,
  children,
}: {
  width?: "narrow" | "default" | "wide" | "full";
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      data-width={width}
      className={cn(
        "page-frame @container relative w-full",
        width === "full" ? "" : "px-4 pt-6 pb-20 sm:px-6 lg:px-10 xl:pt-10",
        className,
      )}
    >
      {children}
    </div>
  );
}
