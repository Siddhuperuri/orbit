"use client";

import { useSyncExternalStore } from "react";

import {
  readPreference,
  resolveTheme,
  setThemePreference,
  subscribeToTheme,
  type ResolvedTheme,
  type ThemePreference,
} from "@/lib/theme/theme";

export function useTheme(): {
  preference: ThemePreference;
  resolved: ResolvedTheme;
  setPreference: (preference: ThemePreference) => void;
} {
  const preference = useSyncExternalStore<ThemePreference>(
    subscribeToTheme,
    readPreference,
    () => "system",
  );
  const resolved = useSyncExternalStore<ResolvedTheme>(
    subscribeToTheme,
    () => resolveTheme(readPreference()),
    () => "light",
  );
  return { preference, resolved, setPreference: setThemePreference };
}
