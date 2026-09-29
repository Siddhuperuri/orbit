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
  { value: "light", label: "Light", hint: "Bright stone background, dark ink.", icon: Sun },
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
      className="border-line gap-0 overflow-hidden rounded-xl border sm:grid-cols-3"
    >
      {OPTIONS.map((option) => (
        <div
          key={option.value}
          className="border-line hover:bg-fill has-[[data-state=checked]]:bg-fg has-[[data-state=checked]]:text-canvas group/theme relative flex flex-col gap-8 p-5 transition-colors duration-500 sm:not-last:border-r"
        >
          <div className="flex items-center justify-between">
            <span className="border-line-strong inline-flex size-9 items-center justify-center rounded-lg border group-has-[[data-state=checked]]/theme:border-current">
              <option.icon className="size-4" strokeWidth={1.5} aria-hidden="true" />
            </span>
            <RadioGroupItem value={option.value} id={`theme-${option.value}`} />
          </div>
          {/* The label covers the whole card, so anywhere on it selects the option. */}
          <Label
            htmlFor={`theme-${option.value}`}
            className="cursor-pointer space-y-0.5 text-current after:absolute after:inset-0 after:content-['']"
          >
            <span className="block text-xl font-medium tracking-tight">{option.label}</span>
            <span className="block text-sm font-normal opacity-75">{option.hint}</span>
          </Label>
        </div>
      ))}
    </RadioGroup>
  );
}
