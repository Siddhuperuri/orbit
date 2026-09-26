"use client";

import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils/cn";

const GLYPHS = " ·.:+*#%@0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");

/**
 * A quiet field of drifting characters -- the kind of texture a terminal leaves when
 * something is thinking. A grid of monospaced cells, each showing a glyph chosen by a
 * slowly moving value noise, so bands of denser and sparser characters roll across
 * the page. Where the pointer passes, cells flare in the signal colour and scramble.
 *
 * Canvas, DPR-aware, paused off-screen, one still frame under `prefers-reduced-motion`,
 * and `aria-hidden`. Sits behind content at low opacity; it is atmosphere, never text.
 */
export function AsciiField({
  className,
  cell = 15,
}: {
  className?: string;
  /** Cell size in CSS px; also the font size. */
  cell?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;
    const ctx = context;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let width = 0;
    let height = 0;
    let cols = 0;
    let rows = 0;
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

    // Cheap smooth value noise from a hash: no library, no allocation per frame.
    function hash(x: number, y: number, z: number): number {
      const n = Math.sin(x * 127.1 + y * 311.7 + z * 74.7) * 43758.5453;
      return n - Math.floor(n);
    }
    function noise(x: number, y: number, z: number): number {
      const xi = Math.floor(x);
      const yi = Math.floor(y);
      const xf = x - xi;
      const yf = y - yi;
      const u = xf * xf * (3 - 2 * xf);
      const v = yf * yf * (3 - 2 * yf);
      const a = hash(xi, yi, z);
      const b = hash(xi + 1, yi, z);
      const c = hash(xi, yi + 1, z);
      const d = hash(xi + 1, yi + 1, z);
      return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
    }

    function resize() {
      const box = (canvas as HTMLCanvasElement).getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = Math.max(1, Math.round(box.width));
      height = Math.max(1, Math.round(box.height));
      (canvas as HTMLCanvasElement).width = Math.round(width * dpr);
      (canvas as HTMLCanvasElement).height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      cols = Math.ceil(width / (cell * 0.62));
      rows = Math.ceil(height / cell);
    }

    function draw() {
      ctx.clearRect(0, 0, width, height);
      const fg = css("--fg", "#000");
      const signal = css("--signal", "#f60");
      ctx.font = `500 ${cell}px ${css("--font-mono", "monospace")}`;
      ctx.textBaseline = "top";
      const step = cell * 0.62;

      for (let row = 0; row < rows; row += 1) {
        for (let col = 0; col < cols; col += 1) {
          const x = col * step;
          const y = row * cell;
          let value =
            noise(col * 0.09, row * 0.11, time) * 0.75 +
            noise(col * 0.23, row * 0.27, time * 1.7) * 0.25;
          let hot = 0;
          if (pointer.active) {
            const d = Math.hypot(pointer.x - x, pointer.y - y);
            if (d < 130) {
              hot = 1 - d / 130;
              value = Math.min(1, value + hot * 0.6);
            }
          }
          // Sparse: only the crests of the noise are drawn, so the page reads as a few
          // drifting clusters of characters, not a wall of them.
          const level = value - 0.5;
          if (level <= 0 && hot === 0) continue;
          const glyph =
            GLYPHS[
              Math.floor(
                Math.max(level, 0) * 2.2 * (GLYPHS.length - 1) + hot * 3 * hash(col, row, time * 9),
              ) % GLYPHS.length
            ];
          if (!glyph || glyph === " ") continue;
          ctx.fillStyle = hot > 0.25 ? signal : fg;
          ctx.globalAlpha = hot > 0.25 ? 0.4 + hot * 0.6 : 0.04 + Math.max(level, 0) * 0.55;
          ctx.fillText(glyph, x, y);
        }
      }
      ctx.globalAlpha = 1;
    }

    function tick(now: number) {
      frame = requestAnimationFrame(tick);
      if (!visible) return;
      // ~24 fps is plenty for a texture, and a third of the work of 60.
      if (now - last < 41) return;
      time += ((now - last) / 1000) * 0.18;
      last = now;
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
  }, [cell]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className={cn("pointer-events-none block size-full select-none", className)}
    />
  );
}
