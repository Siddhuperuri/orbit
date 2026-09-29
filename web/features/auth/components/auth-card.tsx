import type { ReactNode } from "react";

import { KineticText } from "@/components/ui/kinetic-text";

/**
 * The frame for an auth screen: a large serif heading that rises into place, a
 * line beneath it, then the form. Not a bordered card -- beside the brand panel the
 * heading and the form's own fields give the structure, and a box around them would
 * add weight, not clarity.
 */
export function AuthCard({
  title,
  description,
  children,
  footer,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="w-full">
      <h1 className="text-fg text-4xl font-semibold tracking-[-0.035em]">
        <KineticText text={title} />
      </h1>
      {description ? (
        <p className="enter enter-1 text-fg-muted mt-3 text-base">{description}</p>
      ) : null}
      <div className="enter enter-2 mt-10 space-y-5">{children}</div>
      {footer ? (
        <div className="enter enter-3 border-line text-fg-muted mt-10 border-t pt-6 text-base">
          {footer}
        </div>
      ) : null}
    </div>
  );
}
