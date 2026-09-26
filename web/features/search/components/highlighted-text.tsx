import { useMemo } from "react";

import { highlightSegments } from "@/features/search/lib/highlight";

/**
 * Text with the query's words emphasised. Every segment is rendered as a React
 * text node -- document content never passes through an HTML string -- and the
 * `<mark>` carries meaning to assistive tech ("highlighted"), unlike a coloured span.
 */
export function HighlightedText({ text, query }: { text: string; query: string }) {
  const segments = useMemo(() => highlightSegments(text, query), [text, query]);

  return (
    <>
      {segments.map((segment, index) =>
        segment.match ? (
          <mark key={index} className="bg-highlight text-on-highlight rounded-xs px-0.5">
            {segment.text}
          </mark>
        ) : (
          <span key={index}>{segment.text}</span>
        ),
      )}
    </>
  );
}
