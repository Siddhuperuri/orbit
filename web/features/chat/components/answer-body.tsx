"use client";

import { useMemo } from "react";
import Markdown from "react-markdown";

import { CitationMarker } from "@/features/chat/components/citation-marker";
import { handleFromHref, linkCitations } from "@/features/chat/lib/citations";
import { citationsOf } from "@/features/chat/lib/message";
import type { Message } from "@/features/chat/types";
import { cn } from "@/lib/utils/cn";

/**
 * An assistant answer as formatted text with live citation markers.
 *
 * `react-markdown` builds React elements from the Markdown and never touches raw
 * HTML, so a model that emits `<script>` (or is induced to by a poisoned
 * document) produces literal text, not markup. Markers are recognised by rewriting
 * resolved `[S1]` handles to `#cite-S1` links first and mapping those to buttons;
 * every other link opens in a new tab with `noopener`.
 */
export function AnswerBody({
  message,
  onCite,
  muted = false,
}: {
  message: Message;
  onCite: (handle: string) => void;
  /** An answer that is not fully grounded is set visibly quieter (ADR-0006). */
  muted?: boolean;
}) {
  const citations = citationsOf(message);
  const source = useMemo(
    () => linkCitations(message.content, citations),
    [message.content, citations],
  );
  const byHandle = useMemo(
    () => new Map(citations.map((citation) => [citation.handle.toUpperCase(), citation])),
    [citations],
  );

  return (
    <div className={cn("reading", muted && "text-fg-muted")}>
      <Markdown
        components={{
          // A model may write `# Heading`. Rendered as-is that would add an <h1> (or
          // skip levels) inside a page that already has its own outline, so answer
          // headings are shifted to sit beneath the answer: h3 and h4.
          h1: ({ children }) => <h3>{children}</h3>,
          h2: ({ children }) => <h3>{children}</h3>,
          h3: ({ children }) => <h4>{children}</h4>,
          h4: ({ children }) => <h4>{children}</h4>,
          h5: ({ children }) => <h4>{children}</h4>,
          h6: ({ children }) => <h4>{children}</h4>,
          a: ({ href, children }) => {
            const handle = handleFromHref(href);
            if (handle) {
              const citation = byHandle.get(handle);
              return citation ? (
                <CitationMarker citation={citation} onCite={onCite} />
              ) : (
                <>{children}</>
              );
            }
            return (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {source}
      </Markdown>
    </div>
  );
}
