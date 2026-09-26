import { FileText, FileType2, Hash, type LucideIcon } from "lucide-react";

import type { FileKind } from "@/features/documents/lib/file-kind";
import { cn } from "@/lib/utils/cn";

const ICONS: Record<FileKind, LucideIcon> = {
  pdf: FileType2,
  markdown: Hash,
  text: FileText,
  other: FileText,
};

/** A PDF is the common case and gets the one tint; the rest stay neutral. */
const TINTS: Record<FileKind, string> = {
  pdf: "border-danger/50 text-danger",
  markdown: "border-accent/50 text-accent",
  text: "border-line-strong text-fg-muted",
  other: "border-line-strong text-fg-muted",
};

/**
 * A small tile marking a document's file type, so a long list can be scanned by
 * shape before it is read. Decorative: the type is always also written out.
 */
export function FileKindIcon({ kind, className }: { kind: FileKind; className?: string }) {
  const Icon = ICONS[kind];
  return (
    <span
      aria-hidden="true"
      className={cn(
        "inline-flex size-10 shrink-0 items-center justify-center rounded-xl border",
        TINTS[kind],
        className,
      )}
    >
      <Icon className="size-4.5" strokeWidth={1.5} />
    </span>
  );
}
