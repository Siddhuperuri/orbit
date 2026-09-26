import { cn } from "@/lib/utils/cn";

/**
 * A spinner for pending work. Purely visual by default (`aria-hidden`): the
 * *context* -- a button that says "Saving…", a region with `aria-busy` -- is what
 * a screen reader should hear. Pass `label` only when the spinner stands alone.
 */
export function Spinner({ className, label }: { className?: string; label?: string }) {
  return (
    <span
      role={label ? "status" : undefined}
      aria-hidden={label ? undefined : true}
      className="inline-flex"
    >
      <svg
        viewBox="0 0 16 16"
        fill="none"
        className={cn("size-4 animate-spin", className)}
        aria-hidden="true"
      >
        <circle cx="8" cy="8" r="6" stroke="currentColor" strokeOpacity="0.22" strokeWidth="1.25" />
        <circle cx="14" cy="8" r="1.9" fill="currentColor" />
      </svg>
      {label ? <span className="sr-only">{label}</span> : null}
    </span>
  );
}
