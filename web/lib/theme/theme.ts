import { readStorage, StorageKey, writeStorage } from "@/lib/storage";

/**
 * Theme preference: `system` follows the OS, `light` and `dark` override it.
 *
 * The *resolved* theme is written to `<html data-theme>` -- the only thing the
 * CSS tokens key on. `public/theme-init.js` does the same before first paint;
 * this module keeps it correct afterwards (a user changing the setting, the OS
 * switching at sunset, another tab changing it).
 *
 * It is an external store consumed with `useSyncExternalStore` rather than React
 * state, because the source of truth lives outside React: `localStorage`, a media
 * query, and the DOM attribute.
 */

export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

const DARK_QUERY = "(prefers-color-scheme: dark)";
const listeners = new Set<() => void>();

export function readPreference(): ThemePreference {
  const stored = readStorage(StorageKey.theme);
  return stored === "light" || stored === "dark" ? stored : "system";
}

export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  if (preference !== "system") return preference;
  return typeof window !== "undefined" && window.matchMedia(DARK_QUERY).matches ? "dark" : "light";
}

function apply(): void {
  document.documentElement.setAttribute("data-theme", resolveTheme(readPreference()));
}

function emit(): void {
  for (const listener of listeners) listener();
}

export function setThemePreference(preference: ThemePreference): void {
  writeStorage(StorageKey.theme, preference === "system" ? null : preference);
  apply();
  emit();
}

export function subscribeToTheme(listener: () => void): () => void {
  listeners.add(listener);

  const query = window.matchMedia(DARK_QUERY);
  const onSystemChange = () => {
    apply();
    emit();
  };
  const onStorage = (event: StorageEvent) => {
    if (event.key === StorageKey.theme) onSystemChange();
  };
  query.addEventListener("change", onSystemChange);
  window.addEventListener("storage", onStorage);

  return () => {
    listeners.delete(listener);
    query.removeEventListener("change", onSystemChange);
    window.removeEventListener("storage", onStorage);
  };
}
