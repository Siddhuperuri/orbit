"use client";

import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils/cn";

/**
 * Text drawn as an image: a phrase repeated along a spiral, turning slowly, with each
 * glyph a little larger than the last as the arm winds outward -- ORBIT's own words
 * as a galaxy. Rendered to a `<canvas>` (no DOM per character), sized to its box,
 * and DPR-aware.
 *
 * The pointer bends the field: glyphs within reach of the cursor lift toward it and
 * brighten, so the drawing answers to the hand. It pauses while off-screen or in a
 * background tab, draws one still frame under `prefers-reduced-motion`, and is
 * `aria-hidden` -- pure ornament, never content.
 *
 * Colours are read from the theme's CSS variables at draw time, so it follows the
 * palette (and a themed subtree such as the always-dark brand panel) without props.
 */
export function AsciiVortex({
  phrase = "ASK YOUR DOCUMENTS · CHECK EVERY ANSWER · ",
  center = [0.5, 0.5],
  className,
}: {
  phrase?: string;
  /** Where the spiral's eye sits, as fractions of the canvas (x, y). */
  center?: readonly [number, number];
  className?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;
    const ctx = context;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const chars = [...phrase];
    let width = 0;
    let height = 0;
    let dpr = 1;
    let frame = 0;
    let visible = true;
    let time = 0;
    let last = 0;
    const pointer = { x: -9999, y: -9999, active: false };

    function css(name: string, fallback: string): string {
      const value = getComputedStyle(canvas as HTMLCanvasElement)
        .getPropertyValue(name)
        .trim();
      return value || fallback;
    }

    function resize() {
      const box = (canvas as HTMLCanvasElement).getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = Math.max(1, Math.round(box.width));
      height = Math.max(1, Math.round(box.height));
      (canvas as HTMLCanvasElement).width = Math.round(width * dpr);
      (canvas as HTMLCanvasElement).height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function draw() {
      ctx.clearRect(0, 0, width, height);
      const fg = css("--fg", "#fff");
      const signal = css("--signal", "#f60");
      const mono = css("--font-mono", "monospace");
      const cx = width * center[0];
      const cy = height * center[1];
      // Far enough to fill the canvas from wherever the eye sits.
      const reach = Math.hypot(Math.max(cx, width - cx), Math.max(cy, height - cy)) * 1.05;

      ctx.textAlign = "center";
      ctx.textBaseline = "middle";

      // An Archimedean arm: radius grows with the angle, and characters are laid at
      // even arc-length steps, so spacing stays constant as the arm widens.
      const growth = 15; // px of radius gained per radian
      let angle = 1.6;
      let index = 0;
      while (true) {
        const radius = growth * angle;
        if (radius > reach) break;
        const size = 6 + radius * 0.05; // glyphs grow outward
        const a = angle + time;
        let x = cx + Math.cos(a) * radius;
        let y = cy + Math.sin(a) * radius * 0.94;

        let lift = 0;
        if (pointer.active) {
          const dx = pointer.x - x;
          const dy = pointer.y - y;
          const distance = Math.hypot(dx, dy);
          if (distance < 150) {
            lift = 1 - distance / 150;
            x += dx * lift * 0.18;
            y += dy * lift * 0.18;
          }
        }

        ctx.save();
        ctx.translate(x, y);
        ctx.rotate(a + Math.PI / 2);
        ctx.font = `${500} ${size * (1 + lift * 0.45)}px ${mono}`;
        // Every 41st glyph is the signal orange: a single live point along the arm.
        const flag = index % 41 === 0;
        ctx.fillStyle = flag || lift > 0.55 ? signal : fg;
        ctx.globalAlpha = Math.min(1, (flag ? 0.95 : 0.16 + (radius / reach) * 0.7) + lift * 0.5);
        ctx.fillText(chars[index % chars.length] ?? " ", 0, 0);
        ctx.restore();

        // Advance by one glyph's width along the arm.
        angle += (size * 0.98) / Math.max(radius, 24);
        index += 1;
      }
    }

    function tick(now: number) {
      frame = requestAnimationFrame(tick);
      if (!visible) return;
      const dt = last ? Math.min(0.05, (now - last) / 1000) : 0;
      last = now;
      time += dt * 0.07;
      draw();
    }

    function onMove(event: PointerEvent) {
      const box = (canvas as HTMLCanvasElement).getBoundingClientRect();
      pointer.x = event.clientX - box.left;
      pointer.y = event.clientY - box.top;
      pointer.active =
        pointer.x >= 0 && pointer.y >= 0 && pointer.x <= box.width && pointer.y <= box.height;
    }

    resize();
    if (reduced) {
      draw();
    } else {
      frame = requestAnimationFrame(tick);
    }

    const observer = new ResizeObserver(() => {
      resize();
      if (reduced) draw();
    });
    observer.observe(canvas);
    const intersection = new IntersectionObserver(([entry]) => {
      visible = entry?.isIntersecting ?? true;
    });
    intersection.observe(canvas);
    if (!reduced && window.matchMedia("(hover: hover)").matches) {
      window.addEventListener("pointermove", onMove, { passive: true });
    }

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      intersection.disconnect();
      window.removeEventListener("pointermove", onMove);
    };
  }, [phrase, center]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className={cn("pointer-events-none block size-full select-none", className)}
    />
  );
}
