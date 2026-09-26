"use client";

import { Plus } from "lucide-react";
import { useState } from "react";

import { FormError } from "@/components/forms/form-error";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input, NativeSelect } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useToggleDocumentTag } from "@/features/documents/api/use-documents";
import { useDocumentAccess } from "@/features/documents/hooks/use-document-access";
import type { Document } from "@/features/documents/types";
import { useCreateTag, useTags } from "@/features/tags/api/use-tags";
import { TagChip } from "@/features/tags/components/tag-chip";
import { TAG_COLORS } from "@/features/tags/lib/colors";
import type { TagColor } from "@/features/tags/types";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { announce } from "@/lib/a11y/announcer";
import { describeError } from "@/lib/api/describe-error";

const MAX_TAGS_PER_DOCUMENT = 20;

/** The tag chooser: every workspace tag as a checkbox, plus making a new one on the spot. */
function TagPicker({ document }: { document: Document }) {
  const { workspace } = useWorkspace();
  const tags = useTags(workspace.id);
  const toggle = useToggleDocumentTag(workspace.id);
  const create = useCreateTag(workspace.id);
  const [name, setName] = useState("");
  const [color, setColor] = useState<TagColor>("neutral");
  const [failure, setFailure] = useState<unknown>(null);

  const attached = new Set(document.tags.map((tag) => tag.id));
  const atLimit = document.tags.length >= MAX_TAGS_PER_DOCUMENT;

  async function createAndAttach() {
    const trimmed = name.trim();
    if (!trimmed) return;
    setFailure(null);
    try {
      const tag = await create.mutateAsync({ name: trimmed, color });
      await toggle.mutateAsync({ documentId: document.id, tagId: tag.id, attached: true });
      announce(`Created tag ${tag.name} and added it`);
      setName("");
    } catch (error) {
      setFailure(error);
    }
  }

  const description = failure ? describeError(failure) : null;

  return (
    <div className="space-y-3">
      <fieldset>
        <legend className="text-fg mb-2 text-base font-medium">Tags on this document</legend>
        {tags.isPending ? (
          <p className="text-fg-muted text-sm">Loading tags…</p>
        ) : tags.data === undefined ? (
          <p className="text-danger text-sm">Couldn&apos;t load tags.</p>
        ) : tags.data.length === 0 ? (
          <p className="text-fg-muted text-sm">No tags exist yet. Create the first one below.</p>
        ) : (
          <ul className="max-h-56 space-y-0.5 overflow-y-auto">
            {tags.data.map((tag) => {
              const checked = attached.has(tag.id);
              const id = `doc-tag-${tag.id}`;
              const pending = toggle.isPending && toggle.variables?.tagId === tag.id;
              return (
                <li key={tag.id}>
                  <label
                    htmlFor={id}
                    className="hover:bg-fill flex min-h-8 cursor-pointer items-center gap-2.5 rounded-sm px-1.5 py-1 pointer-coarse:min-h-11"
                  >
                    <Checkbox
                      id={id}
                      checked={checked}
                      disabled={pending || (!checked && atLimit)}
                      onCheckedChange={(value) =>
                        toggle.mutate({
                          documentId: document.id,
                          tagId: tag.id,
                          attached: value === true,
                        })
                      }
                    />
                    <TagChip tag={tag} />
                  </label>
                </li>
              );
            })}
          </ul>
        )}
        {atLimit ? (
          <p className="text-fg-muted mt-2 text-xs">
            A document can carry at most {MAX_TAGS_PER_DOCUMENT} tags.
          </p>
        ) : null}
      </fieldset>

      <form
        method="post"
        className="border-line space-y-2 border-t pt-3"
        onSubmit={(event) => {
          event.preventDefault();
          void createAndAttach();
        }}
      >
        <Label htmlFor="new-tag-name" className="text-fg">
          Create a tag
        </Label>
        <div className="flex gap-2">
          <Input
            id="new-tag-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={64}
            autoComplete="off"
            placeholder="Name"
          />
          <NativeSelect
            aria-label="Colour"
            className="w-24 shrink-0"
            value={color}
            onChange={(event) => setColor(event.target.value as TagColor)}
          >
            {TAG_COLORS.map(({ value, label }) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </NativeSelect>
        </div>
        <Button
          type="submit"
          size="sm"
          disabled={name.trim() === "" || atLimit}
          loading={create.isPending}
        >
          Create and add
        </Button>
        <FormError message={description?.message} requestId={description?.requestId} />
      </form>
    </div>
  );
}

/**
 * The document's tags. Everyone who can read the document sees them; only roles that hold
 * `tag:write` get the remove buttons and the picker. A viewer sees chips with nothing to
 * press -- not disabled controls that explain themselves.
 */
export function DocumentTags({ document }: { document: Document }) {
  const { workspace } = useWorkspace();
  const { canTag } = useDocumentAccess();
  const toggle = useToggleDocumentTag(workspace.id);

  return (
    <div>
      {document.tags.length === 0 ? (
        <p className="text-fg-muted text-base">No tags</p>
      ) : (
        <ul aria-label="Tags" className="flex flex-wrap gap-1.5">
          {document.tags.map((tag) => (
            <li key={tag.id} className="max-w-full">
              <TagChip
                tag={tag}
                removing={toggle.isPending && toggle.variables?.tagId === tag.id}
                onRemove={
                  canTag
                    ? () =>
                        toggle.mutate({ documentId: document.id, tagId: tag.id, attached: false })
                    : undefined
                }
              />
            </li>
          ))}
        </ul>
      )}

      {canTag ? (
        <Popover>
          <PopoverTrigger asChild>
            <Button size="sm" variant="ghost" className="mt-1.5 -ml-2">
              <Plus aria-hidden="true" />
              Edit tags
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-80">
            <TagPicker document={document} />
          </PopoverContent>
        </Popover>
      ) : null}
    </div>
  );
}
