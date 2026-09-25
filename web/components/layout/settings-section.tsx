import { useId, type ReactNode } from "react";

/**
 * One section of a settings page: a heading, a line explaining it, and its
 * controls. The heading names the region (`aria-labelledby`), so a screen-reader
 * user can jump between sections by landmark as well as by heading.
 */
export function SettingsSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
}) {
  const headingId = useId();

  return (
    <section
      aria-labelledby={headingId}
      className="border-line border-t py-7 first:border-t-0 first:pt-0"
    >
      <h2 id={headingId} className="text-md text-fg font-semibold">
        {title}
      </h2>
      {description ? (
        <p className="text-fg-muted mt-1 max-w-prose text-base">{description}</p>
      ) : null}
      <div className="mt-4">{children}</div>
    </section>
  );
}
