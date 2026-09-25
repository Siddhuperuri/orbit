import type { BadgeProps } from "@/components/ui/badge";
import type { TagColor } from "@/features/tags/types";

/**
 * A tag's colour is a *theme tone*, not a hex value: the palette is the five tones the
 * design system already verifies for contrast in both themes, so a tag can never be
 * unreadable, and a theme change recolours every tag without touching a row.
 *
 * The names are how the tone actually looks. A tag always carries its name as text --
 * colour is decoration on top of a label, never the label.
 */
export const TAG_COLORS: ReadonlyArray<{ value: TagColor; label: string }> = [
  { value: "neutral", label: "Grey" },
  { value: "accent", label: "Blue" },
  { value: "success", label: "Green" },
  { value: "warning", label: "Amber" },
  { value: "danger", label: "Red" },
];

export const TAG_TONES: Record<TagColor, NonNullable<BadgeProps["tone"]>> = {
  neutral: "neutral",
  accent: "accent",
  success: "success",
  warning: "warning",
  danger: "danger",
};

export function colorLabel(color: TagColor): string {
  return TAG_COLORS.find((entry) => entry.value === color)?.label ?? color;
}
