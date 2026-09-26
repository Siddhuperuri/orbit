"use client";

import { AlertCircle, CheckCircle2, Info } from "lucide-react";
import { Toaster as Sonner } from "sonner";

import { useMediaQuery } from "@/lib/hooks/use-media-query";
import { useTheme } from "@/lib/theme/use-theme";

/**
 * The toast region, styled entirely from the design tokens (Sonner's own styling
 * is switched off with `unstyled`). Sonner provides the parts that are easy to get
 * wrong: an `aria-live` region, pause-on-hover and pause-on-focus, swipe to
 * dismiss, and an `Alt+T` shortcut that moves focus into the region so keyboard
 * users can reach a toast's action before it expires.
 */
export function Toaster() {
  const { resolved } = useTheme();
  const compact = useMediaQuery("(max-width: 640px)");

  return (
    <Sonner
      theme={resolved}
      position={compact ? "bottom-center" : "bottom-right"}
      closeButton
      visibleToasts={4}
      icons={{
        success: <CheckCircle2 className="text-success size-4" aria-hidden="true" />,
        error: <AlertCircle className="text-danger size-4" aria-hidden="true" />,
        info: <Info className="text-fg-muted size-4" aria-hidden="true" />,
      }}
      toastOptions={{
        unstyled: true,
        classNames: {
          toast:
            "group flex w-full items-start gap-3 border border-line bg-surface/90 p-4 text-base text-fg shadow-float backdrop-blur-md",
          title: "font-medium",
          description: "mt-0.5 text-sm text-fg-muted",
          icon: "mt-0.5 shrink-0",
          actionButton:
            "ml-auto shrink-0 border border-line-strong px-2.5 py-1 font-mono text-2xs uppercase tracking-[0.06em] text-fg hover:bg-fg hover:text-canvas transition-colors pointer-coarse:min-h-11",
          closeButton:
            "absolute -top-2 -left-2 flex size-5 items-center justify-center border border-line-strong bg-surface text-fg-muted hover:text-fg",
        },
      }}
    />
  );
}
