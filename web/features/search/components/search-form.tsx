"use client";

import { Search, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils/cn";
import { Segmented, SegmentedItem } from "@/components/ui/segmented";
import { useDocument } from "@/features/documents/api/use-documents";
import { refine, type SearchState } from "@/features/search/lib/search-params";
import {
  MAX_QUERY_CHARACTERS,
  MODE_DESCRIPTIONS,
  MODE_LABELS,
  SEARCH_MODES,
  type SearchMode,
} from "@/features/search/types";

/** The document a search is restricted to, with a way to lift the restriction. */
function ScopeChip({
  workspaceId,
  documentId,
  onClear,
}: {
  workspaceId: string;
  documentId: string;
  onClear: () => void;
}) {
  const { data: document } = useDocument(workspaceId, documentId);
  return (
    <p className="text-fg-muted flex flex-wrap items-center gap-2 text-sm">
      <span>Searching within</span>
      <span className="border-line-strong inline-flex max-w-full items-center gap-1 border py-0.5 pr-0.5 pl-2.5">
        <span className="text-fg truncate font-serif">{document?.title ?? "one document"}</span>
        <button
          type="button"
          onClick={onClear}
          aria-label="Search all documents instead"
          className="hover:bg-fill inline-flex size-5 items-center justify-center rounded-xs"
        >
          <X className="size-3" aria-hidden="true" />
        </button>
      </span>
    </p>
  );
}

/**
 * The search box. It submits on Enter or the button rather than as-you-type: each
 * hybrid search embeds the query with the AI provider, so firing one per keystroke
 * would spend real money and rate limit for results nobody read.
 *
 * The URL is the source of truth for what was searched, so a search survives reload
 * and back/forward and can be shared. The parent keys this form on the URL's query,
 * which resets the field when the address changes.
 */
export function SearchForm({
  workspaceId,
  state,
  onSubmit,
}: {
  workspaceId: string;
  state: SearchState;
  onSubmit: (next: SearchState) => void;
}) {
  const [text, setText] = useState(state.query);
  const [mode, setMode] = useState<SearchMode>(state.mode);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const query = text.trim();
    if (!query) return;
    // A new query starts at page one; keeping the page would land on results the
    // user has not seen the top of, and often on an empty page.
    onSubmit(refine(state, { query, mode }));
  }

  return (
    <form onSubmit={submit} role="search" className="space-y-6">
      {/* An editorial field: the query set large on a rule. The field itself is
          borderless, so the rule carries the text-field focus indicator -- it turns
          accent and a second, heavier accent line draws across it from the left. */}
      <div
        className={cn(
          "border-control hover:border-fg-subtle relative flex items-center gap-4 border-b pb-3 transition-colors duration-500",
          "has-[input:focus-visible]:border-accent",
          "after:bg-accent after:absolute after:inset-x-0 after:-bottom-px after:h-0.5 after:origin-left after:scale-x-0 after:transition-transform after:duration-700 after:ease-out has-[input:focus-visible]:after:scale-x-100",
        )}
      >
        <Label htmlFor="search-query" className="sr-only">
          Search this workspace
        </Label>
        <Search className="text-fg-subtle size-6 shrink-0" strokeWidth={1.5} aria-hidden="true" />
        {/* The box around the field is the visible control; the field itself is borderless. */}
        <input
          id="search-query"
          type="search"
          value={text}
          onChange={(event) => setText(event.target.value)}
          maxLength={MAX_QUERY_CHARACTERS}
          placeholder="Search your documents — a phrase, a name, or a question"
          autoComplete="off"
          autoFocus={!state.query}
          enterKeyHint="search"
          className="text-fg placeholder:text-fg-subtle h-14 min-w-0 flex-1 bg-transparent font-serif text-xl font-light tracking-tight focus-visible:outline-none sm:text-2xl"
        />
        <Button type="submit" variant="primary" size="lg" disabled={text.trim() === ""}>
          Search
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span id="search-mode-label" className="label-micro text-fg-subtle">
          Match by
        </span>
        <Segmented
          aria-labelledby="search-mode-label"
          aria-describedby="search-mode-hint"
          value={mode}
          onValueChange={(value) => {
            const next = value as SearchMode;
            setMode(next);
            // Changing how matching works re-runs the current query at once --
            // it is a refinement of a search already on screen, not a new one.
            if (state.query) onSubmit(refine(state, { mode: next }));
          }}
        >
          {SEARCH_MODES.map((value) => (
            <SegmentedItem key={value} value={value}>
              {MODE_LABELS[value]}
            </SegmentedItem>
          ))}
        </Segmented>
        <span id="search-mode-hint" className="text-fg-subtle text-sm">
          {MODE_DESCRIPTIONS[mode]}
        </span>
      </div>

      {state.documentId ? (
        <ScopeChip
          workspaceId={workspaceId}
          documentId={state.documentId}
          onClear={() => onSubmit(refine(state, { documentId: undefined }))}
        />
      ) : null}
    </form>
  );
}
