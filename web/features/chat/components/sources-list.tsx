"use client";

import { ChevronDown } from "lucide-react";
import Link from "next/link";

import { describeCitationLocation } from "@/features/chat/lib/citations";
import type { Citation } from "@/features/chat/types";
import { routes } from "@/lib/navigation";
import { cn } from "@/lib/utils/cn";

/**
 * The sources behind an answer. Every field is copied from the retrieved chunk's
 * database record -- never from model output (ADR-0006) -- so what is shown here is
 * exactly what the answer was given, and the snippet is verbatim.
 *
 * Each source is a disclosure: collapsed it costs one line; expanded it shows the
 * passage. Activating a marker in the answer opens the matching one.
 */
export function SourcesList({
  messageId,
  workspaceId,
  citations,
  openHandle,
  onToggle,
}: {
  messageId: string;
  workspaceId: string;
  citations: readonly Citation[];
  openHandle: string | null;
  onToggle: (handle: string) => void;
}) {
  if (citations.length === 0) return null;

  return (
    // A plain `div`, not a `<section aria-label>`: a named section is a landmark, and a
    // long thread has one per answer -- dozens of identically named regions in a screen
    // reader's landmark list. The heading is what identifies it.
    <div className="mt-5">
      <h2 className="text-fg-muted mb-1.5 text-xs font-medium tracking-wide uppercase">Sources</h2>
      <ul className="divide-line border-line divide-y border-y">
        {citations.map((citation) => {
          const handle = citation.handle.toUpperCase();
          const open = openHandle === handle;
          const location = describeCitationLocation(citation.location);
          const panelId = `source-${messageId}-${handle}`;

          return (
            <li key={handle} id={panelId}>
              <button
                type="button"
                aria-expanded={open}
                aria-controls={`${panelId}-passage`}
                onClick={() => onToggle(handle)}
                className="hover:bg-sunken/60 flex w-full items-start gap-3 py-2.5 text-left pointer-coarse:min-h-11"
              >
                <span className="bg-accent-soft text-accent-soft-fg mt-0.5 inline-flex h-5 min-w-6 shrink-0 items-center justify-center rounded-sm px-1 font-mono text-xs font-medium">
                  {handle}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="text-fg block truncate font-serif text-base font-medium">
                    {citation.document.title}
                  </span>
                  {location ? (
                    <span className="text-fg-muted block truncate text-sm">{location}</span>
                  ) : null}
                </span>
                <ChevronDown
                  className={cn(
                    "text-fg-muted mt-1 size-4 shrink-0 transition-transform",
                    open && "rotate-180",
                  )}
                  aria-hidden="true"
                />
              </button>

              {open ? (
                <div id={`${panelId}-passage`} className="pr-1 pb-3 pl-9">
                  <blockquote className="reading border-line-strong text-fg-muted border-l-2 pl-3 text-sm">
                    {citation.snippet}
                  </blockquote>
                  <Link
                    href={routes.document(workspaceId, citation.document.id, {
                      passage: citation.chunk.ordinal ?? undefined,
                      version: citation.version.version_number ?? undefined,
                    })}
                    className="text-accent mt-2 inline-block rounded-xs text-sm font-medium hover:underline"
                  >
                    {citation.chunk.ordinal !== null ? "Open at this passage" : "Open document"}
                  </Link>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
