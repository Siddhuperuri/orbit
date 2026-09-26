"use client";

import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils/cn";

/**
 * The horizontal inset every page's content shares with the column rules, so a
 * block that spans columns sits exactly between two rules. `PageContainer` pads by
 * the same amounts.
 */
export const GRID_INSET = "inset-x-4 sm:inset-x-6 lg:inset-x-10";

/** Two columns on a phone, four on a tablet, six on a desktop. */
function Rules({ className }: { className?: string }) {
  return (
    <div
      className={cn("absolute inset-0 grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6", className)}
    >
      <span />
      <span />
      <span className="hidden md:block" />
      <span className="hidden md:block" />
      <span className="hidden xl:block" />
      <span className="hidden xl:block" />
    </div>
  );
}

/**
 * The visible column grid drawn behind the application -- the structure every page
 * is composed on -- plus a second copy of the rules in the accent, revealed only in
 * a soft circle around the pointer, so the grid lights up where you look.
 *
 * Purely decorative (`aria-hidden`, no pointer events). The light is off on touch
 * screens and for anyone who prefers reduced motion. Position is written straight
 * to two CSS variables through the CSSOM, once per frame at most -- no React state,
 * no re-render, and no inline `style` attribute for a Content-Security-Policy to
 * refuse.
 */
export function GridLines({ className }: { className?: string }) {
  const glowRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const glow = glowRef.current;
    if (!glow) return;
    const media = window.matchMedia("(hover: hover) and (prefers-reduced-motion: no-preference)");
    if (!media.matches) return;

    let frame = 0;
    let x = 0;
    let y = 0;

    function apply() {
      frame = 0;
      if (!glow) return;
      const box = glow.getBoundingClientRect();
      glow.style.setProperty("--mx", `${x - box.left}px`);
      glow.style.setProperty("--my", `${y - box.top}px`);
    }

    function onMove(event: PointerEvent) {
      x = event.clientX;
      y = event.clientY;
      if (!frame) frame = requestAnimationFrame(apply);
    }

    function onLeave() {
      glow?.style.setProperty("--mx", "-999px");
      glow?.style.setProperty("--my", "-999px");
    }

    window.addEventListener("pointermove", onMove, { passive: true });
    document.documentElement.addEventListener("pointerleave", onLeave);
    return () => {
      window.removeEventListener("pointermove", onMove);
      document.documentElement.removeEventListener("pointerleave", onLeave);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  return (
    <div
      aria-hidden="true"
      className={cn("pointer-events-none absolute inset-y-0", GRID_INSET, className)}
    >
      <Rules className="grid-rules" />
      <div ref={glowRef} className="grid-glow absolute inset-0">
        <Rules />
      </div>
    </div>
  );
}
