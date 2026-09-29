import { useId, type ReactNode } from "react";

import { cn } from "@/lib/utils/cn";

/**
 * One section of a settings page, set as an editorial row: a hairline across the
 * page, the section's name and a line explaining it in the first third, its
 * controls in the rest. The heading names the region (`aria-labelledby`), so a
 * screen-reader user can jump between sections by landmark as well as by heading.
 *
 * `tone="danger"` is for the section that destroys something: its controls are
 * boxed in the danger colour so they cannot be mistaken for an ordinary preference.
 */
export function SettingsSection({
  title,
  description,
  tone = "default",
  children,
}: {
  title: string;
  description?: ReactNode;
  tone?: "default" | "danger";
  children: ReactNode;
}) {
  const headingId = useId();

  return (
    <section
      aria-labelledby={headingId}
      className="scroll-reveal border-line bg-canvas grid gap-x-10 gap-y-5 border-t py-9 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]"
    >
      <div>
        <h2
          id={headingId}
          className={cn(
            "text-xl font-normal tracking-tight",
            tone === "danger" ? "text-danger" : "text-fg",
          )}
        >
          {title}
        </h2>
        {description ? <p className="text-fg-muted mt-2 text-sm">{description}</p> : null}
      </div>
      <div className={cn(tone === "danger" && "border-danger/40 rounded-xl border p-5")}>
        {children}
      </div>
    </section>
  );
}
