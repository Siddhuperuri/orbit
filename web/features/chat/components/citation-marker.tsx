"use client";

import { describeCitationLocation } from "@/features/chat/lib/citations";
import type { Citation } from "@/features/chat/types";

/**
 * The inline `S1` marker in an answer. It is a button, not a link: activating it
 * reveals the source below the answer, it does not navigate. Its accessible name
 * carries the document and page, so a screen-reader user hears *what* is cited,
 * not just "S1".
 */
export function CitationMarker({
  citation,
  onCite,
}: {
  citation: Citation;
  onCite: (handle: string) => void;
}) {
  const location = describeCitationLocation(citation.location);
  const label = `Source ${citation.handle}: ${citation.document.title}${location ? `, ${location}` : ""}`;

  return (
    <button
      type="button"
      onClick={() => onCite(citation.handle.toUpperCase())}
      aria-label={label}
      title={label}
      className="bg-accent-soft text-accent-soft-fg mx-0.5 inline-flex h-5 min-w-6 -translate-y-px items-center justify-center rounded-sm px-1 align-baseline font-mono text-xs font-medium no-underline hover:opacity-80 pointer-coarse:h-7 pointer-coarse:min-w-8"
    >
      {citation.handle.toUpperCase()}
    </button>
  );
}
