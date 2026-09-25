import type { ReactNode } from "react";

import { cn } from "@/lib/utils/cn";

const WIDTHS = {
  /** Reading and forms: ~44rem. */
  narrow: "max-w-2xl",
  /** Most pages. */
  default: "max-w-5xl",
  /** Dense tables. */
  wide: "max-w-7xl",
  /** Fills the column (chat manages its own layout). */
  full: "max-w-none",
} as const;

/**
 * The padded, width-limited column every page's content sits in. The gutters step
 * up with the viewport (16px on a phone, 32px on a desktop) and the width is
 * capped so that lines of text stay readable on a wide monitor.
 */
export function PageContainer({
  width = "default",
  className,
  children,
}: {
  width?: keyof typeof WIDTHS;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("mx-auto w-full px-4 py-6 sm:px-6 lg:px-8", WIDTHS[width], className)}>
      {children}
    </div>
  );
}
