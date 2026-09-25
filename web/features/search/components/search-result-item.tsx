"use client";

import { ArrowUpRight, MessageSquare } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { HighlightedText } from "@/features/search/components/highlighted-text";
import { explainRelevance, shouldExplainMatch } from "@/features/search/lib/relevance";
import { contentTypeLabel, type SearchMode, type SearchResult } from "@/features/search/types";
import { routes } from "@/lib/navigation";
import { cn } from "@/lib/utils/cn";
import { formatRelativeTime } from "@/lib/utils/format";

/** Where in the document a passage comes from: pages and heading trail. */
function describeLocation(location: SearchResult["location"]): string | null {
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

/**
 * One matching passage.
 *
 * The excerpt is the point, so it is set as reading text (serif). Around it is what
 * a reader needs to judge and act on it: which document, what kind of document, how
 * current it is, where in the document the passage sits, and a way to open the
 * document *at that passage* rather than at its top.
 *
 * What is deliberately absent is the ranking's arithmetic. The API returns each
 * retriever's position and score and the fused RRF score; none of it is shown,
 * because none of it means anything to a reader (see `lib/relevance.ts`). "Why this
 * result" answers the question a person actually has -- did this match my words or
 * my meaning -- which is the part they can do something about.
 */
export function SearchResultItem({
  result,
  query,
  workspaceId,
  mode,
  onAsk,
}: {
  result: SearchResult;
  query: string;
  workspaceId: string;
  mode: SearchMode;
  /** Carries this passage's document into a question. Omitted where asking is not offered. */
  onAsk?: (result: SearchResult) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const location = describeLocation(result.location);
  const long = result.chunk.text.length > 420;
  const relevance = explainRelevance(result);
  const explainMatch = shouldExplainMatch(mode);

  // The passage the answer came from, opened in place -- the same target a chat
  // citation uses, so a result and a citation behave identically.
  const href = routes.document(workspaceId, result.document.id, {
    passage: result.chunk.ordinal,
    version: result.version.version_number,
  });

  return (
    <li className="border-line border-b py-5 first:pt-0 last:border-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="min-w-0">
          <Link
            href={href}
            className="text-md text-fg hover:text-accent rounded-xs font-serif font-semibold [overflow-wrap:anywhere] hover:underline"
          >
            {result.document.title}
          </Link>
        </h2>
        {explainMatch ? (
          <Badge tone={result.matched_by === "both" ? "accent" : "neutral"}>
            {relevance.label}
          </Badge>
        ) : null}
      </div>

      {/* Type, recency, and position -- the three things that decide whether a
          reader opens this result, in one quiet line. */}
      <p className="text-fg-muted mt-0.5 flex flex-wrap items-center gap-x-2 text-sm">
        <span>{contentTypeLabel(result.document.content_type)}</span>
        <span aria-hidden="true">·</span>
        <span>
          Updated{" "}
          <time dateTime={result.document.updated_at}>
            {formatRelativeTime(result.document.updated_at)}
          </time>
        </span>
        {location ? (
          <>
            <span aria-hidden="true">·</span>
            <span>{location}</span>
          </>
        ) : null}
      </p>

      <p
        className={cn(
          "reading text-fg-muted mt-2",
          // Clamped rather than truncated in the data: the whole passage is in the
          // DOM (searchable, selectable) and only its height is limited.
          !expanded && long && "line-clamp-5",
        )}
      >
        <HighlightedText text={result.chunk.text} query={query} />
      </p>

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
        {long ? (
          <button
            type="button"
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
            className="text-accent rounded-xs text-sm font-medium hover:underline"
          >
            {expanded ? "Show less" : "Show full passage"}
          </button>
        ) : null}

        <Link
          href={href}
          className="text-accent inline-flex items-center gap-1 rounded-xs text-sm font-medium hover:underline"
        >
          Open at this passage
          <ArrowUpRight className="size-3.5" aria-hidden="true" />
        </Link>

        {onAsk ? (
          <button
            type="button"
            onClick={() => onAsk(result)}
            className="text-accent inline-flex items-center gap-1 rounded-xs text-sm font-medium hover:underline"
          >
            <MessageSquare className="size-3.5" aria-hidden="true" />
            Ask about this document
          </button>
        ) : null}

        {explainMatch ? (
          <details className="text-sm">
            <summary className="text-fg-muted hover:text-fg cursor-pointer rounded-xs">
              Why this result
            </summary>
            <p className="border-line bg-sunken text-fg-muted mt-2 max-w-prose rounded-md border p-3">
              {relevance.detail}
            </p>
          </details>
        ) : null}
      </div>
    </li>
  );
}
