/**
 * Colour maths for the design-token contrast tests.
 *
 * OKLCH -> OKLab -> linear sRGB -> WCAG relative luminance. Implemented here
 * rather than pulled in as a dependency because it is forty lines and its only
 * consumer is a test.
 */

export interface Oklch {
  l: number;
  c: number;
  h: number;
  alpha: number;
}

/** Parses `oklch(45% 0.125 255)` and `oklch(20% 0.02 265 / 0.42)`. */
export function parseOklch(value: string): Oklch {
  const match = /^oklch\(\s*([\d.]+)%\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*([\d.]+))?\s*\)$/.exec(
    value.trim(),
  );
  if (!match) throw new Error(`Not an oklch() colour: ${value}`);
  const [, l, c, h, alpha] = match;
  return {
    l: Number(l) / 100,
    c: Number(c),
    h: Number(h),
    alpha: alpha === undefined ? 1 : Number(alpha),
  };
}

function toLinearSrgb({ l, c, h }: Oklch): [number, number, number] {
  const hue = (h * Math.PI) / 180;
  const a = c * Math.cos(hue);
  const b = c * Math.sin(hue);

  const l_ = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m_ = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s_ = (l - 0.0894841775 * a - 1.291485548 * b) ** 3;

  return [
    4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
    -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
    -0.0041960863 * l_ - 0.7034186147 * m_ + 1.707614701 * s_,
  ];
}

const clamp01 = (value: number) => Math.min(1, Math.max(0, value));

export function relativeLuminance(colour: Oklch): number {
  // Clamping to the sRGB gamut is what a browser does for an out-of-gamut colour.
  const [r, g, b] = toLinearSrgb(colour).map(clamp01) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG 2.x contrast ratio between two opaque colours. */
export function contrastRatio(foreground: Oklch, background: Oklch): number {
  const a = relativeLuminance(foreground);
  const b = relativeLuminance(background);
  const [lighter, darker] = a > b ? [a, b] : [b, a];
  return (lighter + 0.05) / (darker + 0.05);
}
