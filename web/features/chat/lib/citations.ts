import type { Citation, Grounding } from "@/features/chat/types";

/**
 * Turning the model's `[S1]` handles into links the renderer can recognise.
 *
 * Only handles that resolved to a real citation are converted. The API has already
 * removed any handle that matched no retrieved source (ADR-0006), so a marker the
 * user sees is always backed by a record copied from the database -- and this stays
 * strict about it: an `[S9]` with no citation is left as plain text, never dressed
 * up as a source.
 */

const HANDLE = /\[(S\d{1,4})\]/gi;

export const CITE_PREFIX = "#cite-";

/** Rewrites resolvable `[S1]` markers as Markdown links to `#cite-S1`. */
export function linkCitations(content: string, citations: readonly Citation[]): string {
  const known = new Set(citations.map((citation) => citation.handle.toUpperCase()));
  if (known.size === 0) return content;
  return content.replace(HANDLE, (match, handle: string) =>
    known.has(handle.toUpperCase())
      ? `[${handle.toUpperCase()}](${CITE_PREFIX}${handle.toUpperCase()})`
      : match,
  );
}

/** The handle from a `#cite-S1` href, or null if it is not one. */
export function handleFromHref(href: string | undefined): string | null {
  if (!href?.startsWith(CITE_PREFIX)) return null;
  return href.slice(CITE_PREFIX.length).toUpperCase();
}

/** How a source's position reads: "Pages 3–4 · Heading trail". */
export function describeCitationLocation(location: Citation["location"]): string | null {
  const parts: string[] = [];
  if (location.page_from !== null) {
    parts.push(
      location.page_to !== null && location.page_to !== location.page_from
        ? `Pages ${location.page_from}–${location.page_to}`
        : `Page ${location.page_from}`,
    );
  }
  if (location.heading_path) parts.push(location.heading_path);
  return parts.length > 0 ? parts.join(" · ") : null;
}

export interface GroundingNotice {
  tone: "warning" | "neutral";
  title: string;
  detail: string;
}

/**
 * What to tell the reader about an answer that is not fully grounded.
 *
 * `null` for a grounded answer. Anything else must be *visibly weaker* than one
 * (ADR-0006): the whole value of binding citations is lost if an uncited answer
 * looks exactly like a cited one.
 */
export function groundingNotice(grounding: Grounding | null | undefined): GroundingNotice | null {
  switch (grounding) {
    case "uncited":
      return {
        tone: "warning",
        title: "Not backed by your documents",
        detail:
          "This answer doesn't cite any of your documents, so it can't be checked against them. Treat it with caution.",
      };
    case "insufficient_evidence":
      return {
        tone: "warning",
        title: "Your documents don't fully cover this",
        detail:
          "What follows is limited to what the documents do say. Some of the question is unanswered.",
      };
    case "no_evidence":
      return {
        tone: "neutral",
        title: "Nothing relevant found",
        detail:
          "No passages in your documents matched this question, so there is no source to cite.",
      };
    default:
      return null;
  }
}
