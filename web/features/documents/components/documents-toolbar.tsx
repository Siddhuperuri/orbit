"use client";

import { Search, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input, NativeSelect } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LIFECYCLE_LABELS } from "@/features/documents/lib/lifecycle";
import {
  MAX_SEARCH_LENGTH,
  SORT_OPTIONS,
  isNarrowed,
  toggleTag,
  withoutFilters,
  type DocumentListState,
} from "@/features/documents/lib/list-params";
import type { DocumentSort, ProcessingStatus } from "@/features/documents/types";
import { TagChip } from "@/features/tags/components/tag-chip";
import type { Tag } from "@/features/tags/types";
import { useDebouncedValue } from "@/lib/hooks/use-debounced-value";

/** Long enough that typing "quarterly" does not fire nine requests; short enough to feel live. */
const SEARCH_DEBOUNCE_MS = 300;

/**
 * The filter for the list's *titles*, the status, and the order.
 *
 * All of it runs on the server, so it is correct across every page and not just the one
 * loaded -- and all of it lives in the URL, so a filtered view can be shared and survives
 * a reload. The text box is labelled "Filter by title" and not "Search" on purpose:
 * searching what documents *say* is a different feature (the Search page), and a box that
 * looked like it did that but only matched titles would quietly disappoint.
 */
export function DocumentsToolbar({
  state,
  onChange,
  tags,
  busy,
}: {
  state: DocumentListState;
  onChange: (next: DocumentListState) => void;
  /** Resolves the selected tag ids to names. May be `undefined` while tags load. */
  tags: readonly Tag[] | undefined;
  busy: boolean;
}) {
  const [draft, setDraft] = useState(state.q);
  const debounced = useDebouncedValue(draft, SEARCH_DEBOUNCE_MS);

  // What we last handed to the URL. Distinguishes "the URL changed because we asked it
  // to" (ignore) from "the URL changed under us -- back button, a shared link" (adopt it).
  // Without this, a slow round trip would overwrite what the user had typed since.
  const sent = useRef(state.q);

  useEffect(() => {
    if (debounced.trim() === state.q.trim() || debounced === sent.current) return;
    sent.current = debounced;
    onChange({ ...state, q: debounced });
    // Only the debounced text should trigger this; `state`/`onChange` change every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

  useEffect(() => {
    if (state.q !== sent.current) {
      sent.current = state.q;
      setDraft(state.q);
    }
  }, [state.q]);

  const selected = state.tags.map((id) => ({ id, tag: tags?.find((tag) => tag.id === id) }));

  return (
    <div role="search" aria-label="Filter documents" className="mb-4 space-y-3">
      <div className="flex flex-wrap items-center gap-2.5">
        <div className="min-w-48 flex-1">
          <Label htmlFor="document-title-filter" className="sr-only">
            Filter by title
          </Label>
          <div className="relative">
            <Search
              className="text-fg-subtle pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
              aria-hidden="true"
            />
            <Input
              id="document-title-filter"
              type="search"
              value={draft}
              maxLength={MAX_SEARCH_LENGTH}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Filter by title…"
              autoComplete="off"
              className="pl-9"
              aria-busy={busy || undefined}
            />
          </div>
        </div>

        <div>
          <Label htmlFor="document-status-filter" className="sr-only">
            Status
          </Label>
          <NativeSelect
            id="document-status-filter"
            className="w-38"
            value={state.status ?? ""}
            onChange={(event) =>
              onChange({
                ...state,
                status: (event.target.value || undefined) as ProcessingStatus | undefined,
              })
            }
          >
            <option value="">All statuses</option>
            {(["ready", "processing", "pending", "failed"] as const).map((value) => (
              <option key={value} value={value}>
                {LIFECYCLE_LABELS[value === "pending" ? "queued" : value]}
              </option>
            ))}
          </NativeSelect>
        </div>

        <div>
          <Label htmlFor="document-sort" className="sr-only">
            Sort by
          </Label>
          <NativeSelect
            id="document-sort"
            className="w-42"
            value={state.sort}
            onChange={(event) => onChange({ ...state, sort: event.target.value as DocumentSort })}
          >
            {SORT_OPTIONS.map(({ value, label }) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </NativeSelect>
        </div>
      </div>

      {selected.length > 0 ||
      isNarrowed({ ...state, folder: { kind: "all" }, archive: "active" }) ? (
        <div className="flex flex-wrap items-center gap-2">
          {selected.length > 0 ? (
            <ul aria-label="Filtering by tags" className="flex flex-wrap items-center gap-1.5">
              {selected.map(({ id, tag }) => (
                <li key={id}>
                  <TagChip
                    tag={tag ?? { name: "Deleted tag", color: "neutral" }}
                    onRemove={() => onChange(toggleTag(state, id))}
                  />
                </li>
              ))}
            </ul>
          ) : null}
          <Button size="sm" variant="ghost" onClick={() => onChange(withoutFilters(state))}>
            <X aria-hidden="true" />
            Clear filters
          </Button>
        </div>
      ) : null}
    </div>
  );
}
