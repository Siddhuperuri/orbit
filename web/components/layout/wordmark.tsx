import { cn } from "@/lib/utils/cn";

/**
 * The ORBIT mark: a ring with one body on it. The only ornament in the interface,
 * and deliberately a single stroke -- it should read as a typographic detail, not
 * a logo demanding attention.
 */
export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("text-fg inline-flex items-center gap-2", className)}>
      <svg viewBox="0 0 20 20" className="size-5" fill="none" aria-hidden="true">
        <circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="15" cy="5" r="2.25" fill="var(--accent)" />
      </svg>
      <span className="text-base font-semibold tracking-[0.14em]">ORBIT</span>
    </span>
  );
}
