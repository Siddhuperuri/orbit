"use client";

import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils/cn";

const RINGS = [
  {
    rx: 150,
    ry: 54,
    opacity: 0.55,
    trail: "animate-orbit-trail-fast",
    depth: "orbit-depth-3",
    moon: [-136.3, 40.5, 7],
  },
  {
    rx: 230,
    ry: 84,
    opacity: 0.34,
    trail: "animate-orbit-trail",
    depth: "orbit-depth-2",
    moon: [139, -121, 5],
  },
  {
    rx: 320,
    ry: 118,
    opacity: 0.2,
    trail: "animate-orbit-trail-slow",
    depth: "orbit-depth-1",
    moon: [-54.9, 145.8, 4],
  },
] as const;

/**
 * The product's one piece of artwork: a body inside tilted rings, a moon on each,
 * a short trail of light running round each ring, and the whole system turning very
 * slowly. Each ring sits at its own depth and shifts with the pointer by a different
 * amount (`--px`/`--py`, written through the CSSOM), so the drawing reads as layered
 * space rather than a flat picture.
 *
 * Pure ornament -- `aria-hidden`. With reduced motion, or no fine pointer, it holds
 * still.
 */
export function OrbitArt({
  className,
  body = true,
}: {
  className?: string;
  /** Draw the central body and its glow. Off where the artwork sits behind text. */
  body?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const media = window.matchMedia("(hover: hover) and (prefers-reduced-motion: no-preference)");
    if (!media.matches) return;

    let frame = 0;
    let x = 0;
    let y = 0;

    function apply() {
      frame = 0;
      element?.style.setProperty("--px", x.toFixed(3));
      element?.style.setProperty("--py", y.toFixed(3));
    }

    function onMove(event: PointerEvent) {
      // -1..1 across the window, from its centre.
      x = (event.clientX / window.innerWidth) * 2 - 1;
      y = (event.clientY / window.innerHeight) * 2 - 1;
      if (!frame) frame = requestAnimationFrame(apply);
    }

    window.addEventListener("pointermove", onMove, { passive: true });
    return () => {
      window.removeEventListener("pointermove", onMove);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  return (
    <div ref={ref} aria-hidden="true" className={cn("pointer-events-none select-none", className)}>
      <svg viewBox="-360 -360 720 720" className="size-full overflow-visible" fill="none">
        <defs>
          <radialGradient id="orbit-art-glow">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.5" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </radialGradient>
        </defs>
        {body ? <circle r="230" fill="url(#orbit-art-glow)" className="orbit-depth-1" /> : null}
        <g className="animate-orbit-spin origin-center [transform-box:view-box]">
          {RINGS.map((ring) => (
            <g key={ring.rx} className={ring.depth}>
              <g transform="rotate(-24)">
                <ellipse
                  rx={ring.rx}
                  ry={ring.ry}
                  stroke="var(--fg)"
                  strokeOpacity={ring.opacity}
                  strokeWidth="1"
                />
                <ellipse
                  rx={ring.rx}
                  ry={ring.ry}
                  pathLength={100}
                  stroke="var(--accent)"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeDasharray="7 93"
                  className={ring.trail}
                />
              </g>
              <circle
                cx={ring.moon[0]}
                cy={ring.moon[1]}
                r={ring.moon[2]}
                fill={ring.rx === 150 ? "var(--accent)" : "var(--fg)"}
                fillOpacity={ring.rx === 150 ? 1 : 0.8}
              />
            </g>
          ))}
        </g>
        {body ? (
          <g className="orbit-depth-3">
            <circle r="48" fill="var(--fg)" />
            <circle r="48" fill="url(#orbit-art-glow)" />
          </g>
        ) : null}
      </svg>
    </div>
  );
}
