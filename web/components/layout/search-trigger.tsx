"use client";

import { Search } from "lucide-react";
import { useSyncExternalStore } from "react";

import { Kbd } from "@/components/ui/kbd";

const subscribeNever = () => () => undefined;

/** `⌘K` on Apple platforms, `Ctrl K` elsewhere. Server-rendered as `Ctrl K`; swapped after hydration. */
export function useShortcutLabel(): string {
  return useSyncExternalStore(
    subscribeNever,
    () => (/Mac|iPhone|iPad/i.test(navigator.platform) ? "⌘K" : "Ctrl K"),
    () => "Ctrl K",
  );
}

/**
 * The command palette's entry point. On a wide screen it looks like a search
 * field so it reads as one; on a phone it collapses to an icon button. Either
 * way it is a `<button>` -- it opens a dialog rather than accepting text itself.
 */
export function SearchTrigger({ onOpen }: { onOpen: () => void }) {
  const shortcut = useShortcutLabel();

  return (
    <>
      <button
        type="button"
        onClick={onOpen}
        aria-haspopup="dialog"
        aria-keyshortcuts="Control+K Meta+K"
        className="border-control bg-surface text-fg-muted hover:border-fg-subtle hover:text-fg hidden h-8 w-64 items-center gap-2 rounded-md border px-2.5 text-left text-base transition-colors sm:flex lg:w-80"
      >
        <Search className="size-4 shrink-0" aria-hidden="true" />
        <span className="flex-1 truncate">Search or jump to…</span>
        <Kbd aria-hidden="true">{shortcut}</Kbd>
      </button>

      <button
        type="button"
        onClick={onOpen}
        aria-haspopup="dialog"
        aria-label="Search or jump to"
        className="text-fg-muted hover:bg-sunken hover:text-fg inline-flex size-8 items-center justify-center rounded-md sm:hidden pointer-coarse:size-11"
      >
        <Search className="size-4" aria-hidden="true" />
      </button>
    </>
  );
}
