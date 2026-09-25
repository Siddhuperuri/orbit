import { X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { TAG_TONES } from "@/features/tags/lib/colors";
import type { TagColor } from "@/features/tags/types";

/**
 * A tag: its name, in its tone. The name is always there as text, so colour is
 * decoration and never the only carrier of meaning -- a tag reads the same on a
 * monochrome display or to a screen reader.
 *
 * Removable only when the caller passes `onRemove`, which callers do only for people
 * who may change tags: a viewer sees a chip with nothing to press.
 */
export function TagChip({
  tag,
  onRemove,
  removing = false,
}: {
  tag: { name: string; color: TagColor };
  onRemove?: () => void;
  removing?: boolean;
}) {
  return (
    <Badge tone={TAG_TONES[tag.color]} className="max-w-full">
      <span className="min-w-0 truncate">{tag.name}</span>
      {onRemove ? (
        <button
          type="button"
          onClick={onRemove}
          disabled={removing}
          aria-label={`Remove tag ${tag.name}`}
          className="-mr-0.5 inline-flex rounded-xs opacity-70 hover:opacity-100 disabled:opacity-40"
        >
          <X aria-hidden="true" />
        </button>
      ) : null}
    </Badge>
  );
}
