import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { contrastRatio, parseOklch } from "@/test/color";

/**
 * The design tokens are the accessibility contract for colour. This parses the
 * real stylesheet, so editing a token in `globals.css` and breaking contrast
 * fails here -- rather than surfacing in an audit after the palette has spread
 * across every screen.
 */

const css = readFileSync(path.join(process.cwd(), "app", "globals.css"), "utf-8");

function tokensOf(selector: string): Map<string, string> {
  const start = css.indexOf(`${selector} {`);
  if (start === -1) throw new Error(`No ${selector} block in globals.css`);
  const end = css.indexOf("\n}", start);
  const tokens = new Map<string, string>();
  for (const match of css.slice(start, end).matchAll(/^\s*--([a-z-]+):\s*(oklch\([^)]*\));/gm)) {
    tokens.set(match[1]!, match[2]!);
  }
  return tokens;
}

const themes = { light: tokensOf(":root"), dark: tokensOf('[data-theme="dark"]') };

function ratio(theme: keyof typeof themes, foreground: string, background: string): number {
  const tokens = themes[theme];
  const fg = tokens.get(foreground);
  const bg = tokens.get(background);
  if (!fg || !bg) throw new Error(`Missing token: ${foreground} or ${background} in ${theme}`);
  return contrastRatio(parseOklch(fg), parseOklch(bg));
}

/** [foreground, background] pairs that carry text. WCAG 1.4.3: 4.5:1. */
const textPairs: Array<[string, string]> = [
  ["fg", "canvas"],
  ["fg", "surface"],
  ["fg", "sunken"],
  ["fg-muted", "canvas"],
  ["fg-muted", "surface"],
  ["fg-muted", "sunken"],
  ["fg-subtle", "canvas"],
  ["fg-subtle", "surface"],
  ["fg-subtle", "sunken"],
  ["accent", "canvas"],
  ["accent", "surface"],
  ["accent", "sunken"],
  ["accent-soft-fg", "accent-soft"],
  ["on-accent", "accent-solid"],
  ["on-accent", "accent-solid-hover"],
  ["on-danger", "danger-solid"],
  ["success", "canvas"],
  ["success", "surface"],
  ["success", "success-soft"],
  ["warning", "canvas"],
  ["warning", "surface"],
  ["warning", "warning-soft"],
  ["danger", "canvas"],
  ["danger", "surface"],
  ["danger", "danger-soft"],
];

/** Non-text indicators. WCAG 1.4.11: 3:1. */
const uiPairs: Array<[string, string]> = [
  ["focus", "canvas"],
  ["focus", "surface"],
  ["control", "canvas"],
  ["control", "surface"],
];

describe.each(Object.keys(themes) as Array<keyof typeof themes>)("%s theme", (theme) => {
  it.each(textPairs)("text %s on %s meets 4.5:1", (foreground, background) => {
    expect(ratio(theme, foreground, background)).toBeGreaterThanOrEqual(4.5);
  });

  it.each(uiPairs)("indicator %s against %s meets 3:1", (foreground, background) => {
    expect(ratio(theme, foreground, background)).toBeGreaterThanOrEqual(3);
  });
});

describe("token parity", () => {
  it("defines the same tokens in both themes", () => {
    expect([...themes.dark.keys()].sort()).toEqual([...themes.light.keys()].sort());
  });
});
