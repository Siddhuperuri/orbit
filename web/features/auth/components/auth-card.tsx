import type { ReactNode } from "react";

/**
 * The frame for an auth screen: a heading, an optional line beneath it, then the
 * form. Not a bordered card -- on a plain page the heading and the form's own
 * fields give the structure, and a box around them would add weight, not
 * clarity.
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
      <h1 className="text-fg text-xl font-semibold">{title}</h1>
      {description ? <p className="text-fg-muted mt-1.5 text-base">{description}</p> : null}
      <div className="mt-6 space-y-5">{children}</div>
      {footer ? (
        <div className="border-line text-fg-muted mt-8 border-t pt-5 text-base">{footer}</div>
      ) : null}
    </div>
  );
}
