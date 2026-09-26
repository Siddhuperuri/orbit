"use client";

import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";

import { announce } from "@/lib/a11y/announcer";
import { cn } from "@/lib/utils/cn";

/**
 * Copies text and confirms it. The confirmation is announced as well as shown:
 * the icon swap alone is invisible to a screen reader.
 */
export function CopyButton({
  value,
  label,
  className,
}: {
  value: string;
  /** What is being copied, for the accessible name: "Copy request ID". */
  label: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2_000);
    return () => clearTimeout(timer);
  }, [copied]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      announce(`${label}: copied`);
    } catch {
      // Clipboard access can be denied (insecure context, permissions policy).
      // The value is on screen and selectable, so nothing is lost.
      announce("Could not copy. Select the text and copy it manually.", "assertive");
    }
  }

  return (
    <button
      type="button"
      onClick={() => void copy()}
      aria-label={label}
      className={cn(
        "text-fg-muted hover:bg-fill hover:text-fg inline-flex size-6 items-center justify-center rounded-sm pointer-coarse:size-9",
        className,
      )}
    >
      {copied ? (
        <Check className="size-3.5" aria-hidden="true" />
      ) : (
        <Copy className="size-3.5" aria-hidden="true" />
      )}
    </button>
  );
}
