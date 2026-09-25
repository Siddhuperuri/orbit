"use client";

import { Search, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input, NativeSelect } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
      <span className="border-line bg-sunken inline-flex max-w-full items-center gap-1 rounded-sm border py-0.5 pr-0.5 pl-2">
        <span className="text-fg truncate font-serif">{document?.title ?? "one document"}</span>
        <button
          type="button"
          onClick={onClear}
          aria-label="Search all documents instead"
          className="hover:bg-line inline-flex size-5 items-center justify-center rounded-xs"
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
    <form onSubmit={submit} role="search" className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row">
        <div className="flex-1">
          <Label htmlFor="search-query" className="sr-only">
            Search this workspace
          </Label>
          <div className="relative">
            <Search
              className="text-fg-muted pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2"
              aria-hidden="true"
            />
            <Input
              id="search-query"
              type="search"
              value={text}
              onChange={(event) => setText(event.target.value)}
              maxLength={MAX_QUERY_CHARACTERS}
              placeholder="Search your documents"
              autoComplete="off"
              autoFocus={!state.query}
              enterKeyHint="search"
              className="text-md h-10 pl-8 pointer-coarse:h-11"
            />
          </div>
        </div>
        <Button type="submit" variant="primary" size="lg" disabled={text.trim() === ""}>
          Search
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Label htmlFor="search-mode" className="text-fg-muted">
          Match by
        </Label>
        <NativeSelect
          id="search-mode"
          className="w-40"
          value={mode}
          aria-describedby="search-mode-hint"
          onChange={(event) => {
            const next = event.target.value as SearchMode;
            setMode(next);
            // Changing how matching works re-runs the current query at once --
            // it is a refinement of a search already on screen, not a new one.
            if (state.query) onSubmit(refine(state, { mode: next }));
          }}
        >
          {SEARCH_MODES.map((value) => (
            <option key={value} value={value}>
              {MODE_LABELS[value]}
            </option>
          ))}
        </NativeSelect>
        <span id="search-mode-hint" className="text-fg-muted text-sm">
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
