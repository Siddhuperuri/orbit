"use client";

import { ChevronDown } from "lucide-react";
import { useMemo } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useDocuments } from "@/features/documents/api/use-documents";
import { MAX_SCOPED_DOCUMENTS } from "@/features/chat/types";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { pluralize } from "@/lib/utils/format";

/**
 * Limits which documents an answer may draw on. The default -- nothing selected --
 * is the whole workspace; choosing documents narrows it, and the API never widens
 * it (an id from another workspace matches nothing).
 *
 * Only documents that are `ready` are offered: one still processing has no
 * passages to retrieve, so scoping to it would produce an answer from nothing.
 */
export function ScopePicker({
  selected,
  onChange,
  disabled,
}: {
  selected: readonly string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
}) {
  const { workspace } = useWorkspace();
  const documents = useDocuments(workspace.id, { status: "ready" });
  const items = useMemo(
    () => documents.data?.pages.flatMap((page) => page.items) ?? [],
    [documents.data],
  );

  const label = selected.length === 0 ? "All documents" : pluralize(selected.length, "document");
  const atLimit = selected.length >= MAX_SCOPED_DOCUMENTS;

  function toggle(id: string, checked: boolean) {
    onChange(checked ? [...selected, id] : selected.filter((existing) => existing !== id));
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          size="sm"
          variant="ghost"
          disabled={disabled}
          aria-label={`Answer from: ${label}. Change`}
        >
          <span className="text-fg-subtle">Answer from</span>
          <span className="text-fg">{label}</span>
          <ChevronDown aria-hidden="true" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-96">
        <fieldset>
          <legend className="mb-2 flex w-full items-center justify-between">
            <span className="text-fg text-base font-medium">Answer from</span>
            {selected.length > 0 ? (
              <button
                type="button"
                onClick={() => onChange([])}
                className="text-accent rounded-xs text-sm font-medium hover:underline"
              >
                Use all documents
              </button>
            ) : null}
          </legend>

          {documents.isPending ? (
            <p className="text-fg-muted py-4 text-sm">Loading documents…</p>
          ) : items.length === 0 ? (
            <p className="text-fg-muted py-4 text-sm">
              No documents are ready yet. Answers will draw on documents once they finish
              processing.
            </p>
          ) : (
            <ul className="max-h-64 space-y-0.5 overflow-y-auto">
              {items.map((document) => {
                const checked = selected.includes(document.id);
                const id = `scope-${document.id}`;
                return (
                  <li key={document.id}>
                    <label
                      htmlFor={id}
                      className="hover:bg-fill flex min-h-8 cursor-pointer items-center gap-2.5 rounded-sm px-1.5 py-1 pointer-coarse:min-h-11"
                    >
                      <Checkbox
                        id={id}
                        checked={checked}
                        disabled={!checked && atLimit}
                        onCheckedChange={(value) => toggle(document.id, value === true)}
                      />
                      <span className="text-fg min-w-0 flex-1 truncate font-serif text-base">
                        {document.title}
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}

          {documents.hasNextPage ? (
            <Button
              size="sm"
              className="mt-2"
              loading={documents.isFetchingNextPage}
              onClick={() => void documents.fetchNextPage()}
            >
              Load more
            </Button>
          ) : null}
          {atLimit ? (
            <p className="text-fg-muted mt-2 text-xs">
              A question can draw on at most {MAX_SCOPED_DOCUMENTS} documents.
            </p>
          ) : null}
        </fieldset>
      </PopoverContent>
    </Popover>
  );
}
