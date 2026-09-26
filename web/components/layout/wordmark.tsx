import { cn } from "@/lib/utils/cn";

/**
 * The ORBIT mark: a body with one tilted ring and a single satellite on it, the
 * satellite in the accent. It reads at 16px as a planet and at 64px as a small
 * drawing, and it is the only piece of ornament the interface repeats.
 *
 * Inside a `group` (the home link), pointing at it swings the ring and its
 * satellite a quarter-turn round the body -- the one playful gesture in the chrome.
 */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={cn("size-6", className)} fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="4.6" fill="currentColor" />
      <g className="origin-center transition-transform duration-1000 ease-out [transform-box:view-box] group-hover:rotate-[38deg]">
        <ellipse
          cx="12"
          cy="12"
          rx="10.2"
          ry="4.1"
          transform="rotate(-28 12 12)"
          stroke="currentColor"
          strokeOpacity="0.5"
          strokeWidth="1.3"
        />
        <circle cx="17.6" cy="6" r="2.1" fill="var(--accent)" />
      </g>
    </svg>
  );
}

/** The mark with the name set beside it. */
export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("text-fg inline-flex items-center gap-2.5", className)}>
      <LogoMark />
      <span className="text-sm font-semibold tracking-[0.32em]">ORBIT</span>
    </span>
  );
}
