"use client";

import { Filter, X } from "lucide-react";
import { useMemo } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { NativeSelect } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useFolders } from "@/features/folders/api/use-folders";
import { buildTree, flattenTree } from "@/features/folders/lib/tree";
import {
  isNarrowed,
  toggleTag,
  toggleType,
  withoutFilters,
  type SearchState,
} from "@/features/search/lib/search-params";
import { CONTENT_TYPES, MAX_TAG_FILTERS, contentTypeLabel } from "@/features/search/types";
import { TagChip } from "@/features/tags/components/tag-chip";
import { useTags } from "@/features/tags/api/use-tags";
import { pluralize } from "@/lib/utils/format";

/**
 * What a search is narrowed to: a folder, tags, and document types.
 *
 * All of it is applied by the API inside the retrievers' own SQL, so a filtered
 * search is *ranked among the matching passages* rather than ranked first and
 * filtered after -- which would quietly return fewer results than asked for, and
 * sometimes none, while claiming there were no matches.
 *
 * Folder and tag semantics match the Documents page exactly: a folder means the
 * documents directly in it, and tags are conjunctive. A filter that meant one
 * thing in one place and something else in another would be a trap.
 */
export function SearchFilters({
  workspaceId,
  state,
  onChange,
}: {
  workspaceId: string;
  state: SearchState;
  onChange: (next: SearchState) => void;
}) {
  const folders = useFolders(workspaceId);
  const tags = useTags(workspaceId);

  const options = useMemo(() => flattenTree(buildTree(folders.data ?? [])), [folders.data]);
  const selectedTags = state.tags.map((id) => ({
    id,
    tag: tags.data?.find((tag) => tag.id === id),
  }));
  const atTagLimit = state.tags.length >= MAX_TAG_FILTERS;

  const summary = [
    state.folder.kind === "folder" ? "folder" : null,
    state.folder.kind === "unfiled" ? "unfiled" : null,
    state.tags.length > 0 ? pluralize(state.tags.length, "tag") : null,
    state.types.length > 0 ? pluralize(state.types.length, "type") : null,
  ].filter(Boolean);

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <Popover>
        <PopoverTrigger asChild>
          <Button size="sm" variant={summary.length > 0 ? "secondary" : "ghost"}>
            <Filter aria-hidden="true" />
            {summary.length > 0 ? `Filtered: ${summary.join(", ")}` : "Filter"}
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-80 space-y-4">
          <div>
            <Label htmlFor="search-folder" className="text-fg-muted mb-1.5 block">
              Folder
            </Label>
            <NativeSelect
              id="search-folder"
              className="w-full"
              value={
                state.folder.kind === "folder"
                  ? state.folder.id
                  : state.folder.kind === "unfiled"
                    ? "none"
                    : ""
              }
              onChange={(event) => {
                const value = event.target.value;
                onChange({
                  ...state,
                  page: 0,
                  folder:
                    value === ""
                      ? { kind: "all" }
                      : value === "none"
                        ? { kind: "unfiled" }
                        : { kind: "folder", id: value },
                });
              }}
            >
              <option value="">Anywhere</option>
              <option value="none">Not in a folder</option>
              {options.map(({ folder, path, depth }) => (
                <option key={folder.id} value={folder.id}>
                  {`${"  ".repeat(depth)}${folder.name}`}
                  {depth > 0 ? ` — ${path}` : ""}
                </option>
              ))}
            </NativeSelect>
            {/* Said plainly: the API filters on the folder itself, not its subtree. */}
            <p className="text-fg-subtle mt-1 text-xs">
              Documents filed directly in that folder, not its subfolders.
            </p>
          </div>

          <fieldset>
            <legend className="text-fg-muted mb-1.5 text-base">Document type</legend>
            <ul className="space-y-0.5">
              {CONTENT_TYPES.map((type) => {
                const id = `search-type-${type.replace(/\W/g, "-")}`;
                return (
                  <li key={type}>
                    <label
                      htmlFor={id}
                      className="hover:bg-fill flex min-h-8 cursor-pointer items-center gap-2.5 rounded-sm px-1.5 py-1 pointer-coarse:min-h-11"
                    >
                      <Checkbox
                        id={id}
                        checked={state.types.includes(type)}
                        onCheckedChange={() => onChange(toggleType(state, type))}
                      />
                      <span className="text-fg text-base">{contentTypeLabel(type)}</span>
                    </label>
                  </li>
                );
              })}
            </ul>
          </fieldset>

          <fieldset>
            <legend className="text-fg-muted mb-1.5 text-base">Tags</legend>
            {tags.data === undefined ? (
              <p className="text-fg-muted text-sm">Loading tags…</p>
            ) : tags.data.length === 0 ? (
              <p className="text-fg-muted text-sm">This workspace has no tags yet.</p>
            ) : (
              <ul className="max-h-48 space-y-0.5 overflow-y-auto">
                {tags.data.map((tag) => {
                  const checked = state.tags.includes(tag.id);
                  const id = `search-tag-${tag.id}`;
                  return (
                    <li key={tag.id}>
                      <label
                        htmlFor={id}
                        className="hover:bg-fill flex min-h-8 cursor-pointer items-center gap-2.5 rounded-sm px-1.5 py-1 pointer-coarse:min-h-11"
                      >
                        <Checkbox
                          id={id}
                          checked={checked}
                          disabled={!checked && atTagLimit}
                          onCheckedChange={() => onChange(toggleTag(state, tag.id))}
                        />
                        <TagChip tag={tag} />
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
            <p className="text-fg-subtle mt-1 text-xs">
              {atTagLimit
                ? `At most ${MAX_TAG_FILTERS} tags.`
                : "A document must carry every tag you pick."}
            </p>
          </fieldset>
        </PopoverContent>
      </Popover>

      {selectedTags.length > 0 ? (
        <ul aria-label="Filtering by tags" className="flex flex-wrap items-center gap-1.5">
          {selectedTags.map(({ id, tag }) => (
            <li key={id}>
              <TagChip
                tag={tag ?? { name: "Deleted tag", color: "neutral" }}
                onRemove={() => onChange(toggleTag(state, id))}
              />
            </li>
          ))}
        </ul>
      ) : null}

      {isNarrowed(state) ? (
        <Button size="sm" variant="ghost" onClick={() => onChange(withoutFilters(state))}>
          <X aria-hidden="true" />
          Clear filters
        </Button>
      ) : null}
    </div>
  );
}
