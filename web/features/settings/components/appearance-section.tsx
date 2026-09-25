"use client";

import { Monitor, Moon, Sun, type LucideIcon } from "lucide-react";

import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import type { ThemePreference } from "@/lib/theme/theme";
import { useTheme } from "@/lib/theme/use-theme";

const OPTIONS: Array<{ value: ThemePreference; label: string; hint: string; icon: LucideIcon }> = [
  {
    value: "system",
    label: "Match system",
    hint: "Follows your device's light or dark setting.",
    icon: Monitor,
  },
  { value: "light", label: "Light", hint: "Warm paper background, dark text.", icon: Sun },
  { value: "dark", label: "Dark", hint: "Low-glare background for dim rooms.", icon: Moon },
];

/** Theme choice. Stored in this browser only; it is a preference about a screen, not about an account. */
export function AppearanceSection() {
  const { preference, setPreference } = useTheme();

  return (
    <RadioGroup
      value={preference}
      onValueChange={(value) => setPreference(value as ThemePreference)}
      aria-label="Theme"
      className="max-w-md gap-1"
    >
      {OPTIONS.map((option) => (
        <div
          key={option.value}
          className="hover:bg-sunken has-[[data-state=checked]]:border-line has-[[data-state=checked]]:bg-sunken flex items-start gap-3 rounded-md border border-transparent px-2 py-2"
        >
          <RadioGroupItem value={option.value} id={`theme-${option.value}`} className="mt-0.5" />
          <Label htmlFor={`theme-${option.value}`} className="flex-1 cursor-pointer space-y-0.5">
            <span className="flex items-center gap-2">
              <option.icon className="text-fg-muted size-4" aria-hidden="true" />
              {option.label}
            </span>
            <span className="text-fg-muted block text-sm font-normal">{option.hint}</span>
          </Label>
        </div>
      ))}
    </RadioGroup>
  );
}
