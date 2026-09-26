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
        className="border-line text-fg-subtle hover:border-fg-subtle hover:text-fg group hidden h-9 w-64 items-center gap-3 border px-3 text-left text-sm transition-colors duration-300 sm:flex lg:w-80"
      >
        <Search
          className="size-4 shrink-0 transition-transform duration-500 ease-out group-hover:scale-110"
          strokeWidth={1.75}
          aria-hidden="true"
        />
        <span className="flex-1 truncate">Search or jump to…</span>
        <Kbd aria-hidden="true">{shortcut}</Kbd>
      </button>

      <button
        type="button"
        onClick={onOpen}
        aria-haspopup="dialog"
        aria-label="Search or jump to"
        className="text-fg-muted hover:bg-fill hover:text-fg inline-flex size-9 items-center justify-center rounded-md sm:hidden pointer-coarse:size-11"
      >
        <Search className="size-4" aria-hidden="true" />
      </button>
    </>
  );
}
