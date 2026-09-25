/**
 * Display formatting. Centralised so the same value never reads two ways on two
 * screens, and so locale handling has one home.
 */

const UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

/** `52428800` -> `50 MB`. Binary units, labelled the way file managers label them. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const fractionDigits = unit === 0 || value >= 100 ? 0 : value >= 10 ? 1 : 2;
  return `${new Intl.NumberFormat("en", { maximumFractionDigits: fractionDigits }).format(value)} ${UNITS[unit]}`;
}

const dateTime = new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" });
const dateOnly = new Intl.DateTimeFormat("en", { dateStyle: "medium" });

export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : dateTime.format(date);
}

export function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : dateOnly.format(date);
}

const relative = new Intl.RelativeTimeFormat("en", { numeric: "auto", style: "short" });

const STEPS: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ["year", 365 * 24 * 3600],
  ["month", 30 * 24 * 3600],
  ["week", 7 * 24 * 3600],
  ["day", 24 * 3600],
  ["hour", 3600],
  ["minute", 60],
];

/** `2 hr. ago`, `yesterday`, `now`. Falls back to a date beyond a few weeks. */
export function formatRelativeTime(iso: string, now: number = Date.now()): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const seconds = Math.round((then - now) / 1000);
  const absolute = Math.abs(seconds);
  if (absolute < 45) return "just now";
  if (absolute > 30 * 24 * 3600) return formatDate(iso);
  for (const [unit, size] of STEPS) {
    if (absolute >= size) return relative.format(Math.round(seconds / size), unit);
  }
  return relative.format(Math.round(seconds / 60), "minute");
}

export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${new Intl.NumberFormat("en").format(count)} ${count === 1 ? singular : plural}`;
}

/** Up to two initials for an avatar. Never throws on an empty or one-word name. */
export function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  const first = parts[0]!;
  const last = parts.length > 1 ? parts[parts.length - 1]! : "";
  return `${first.charAt(0)}${last.charAt(0)}`.toUpperCase();
}

/** A short, stable form of a UUID for places that must show an id (members list). */
export function shortId(id: string): string {
  return id.slice(0, 8);
}
