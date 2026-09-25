"use client";

import { UploadButton } from "@/features/documents/components/upload-button";
import { useFileDrop } from "@/features/documents/hooks/use-file-drop";
import { useUploadPolicy } from "@/features/documents/upload/use-upload-policy";
import { useUploadQueue } from "@/features/documents/upload/upload-queue-provider";
import { describeFormats } from "@/features/documents/upload/validate";
import { cn } from "@/lib/utils/cn";
import { formatBytes } from "@/lib/utils/format";

/**
 * The large drop target shown when there is nothing to list yet. It is the empty state's
 * call to action: it says what to do, what is accepted (from the deployment's own policy,
 * not a copy of it), and where the files will go -- and the button beside the words is the
 * keyboard- and screen-reader-operable route to the same action.
 */
export function UploadDropzone({
  folderId,
  destination,
  title,
}: {
  folderId?: string;
  /** Names where files will land: "Reports". Omitted means the top level. */
  destination?: string;
  title?: string;
}) {
  const { add } = useUploadQueue();
  const policy = useUploadPolicy();
  const { dragging, dropProps } = useFileDrop((files) => add(files, { folderId }));

  return (
    <div
      {...dropProps}
      className={cn(
        "mx-auto flex max-w-xl flex-col items-center rounded-lg border border-dashed px-6 py-14 text-center transition-colors",
        dragging ? "border-accent bg-accent-soft" : "border-line-strong bg-surface",
      )}
    >
      <h2 className="text-fg text-lg font-semibold">
        {dragging ? "Drop to upload" : (title ?? "Add your first documents")}
      </h2>
      <p className="text-fg-muted mt-1.5 max-w-sm text-base">
        Upload {describeFormats(policy)} files
        {policy.maxBytes !== null ? `, up to ${formatBytes(policy.maxBytes)} each` : ""}. Once
        they&apos;re processed you can search them and ask questions answered from their contents.
      </p>
      {destination ? (
        <p className="text-fg-muted mt-1 text-sm">
          They&apos;ll be added to <span className="text-fg font-medium">{destination}</span>.
        </p>
      ) : null}
      <div className="mt-5">
        <UploadButton folderId={folderId} />
      </div>
      <p className="text-fg-subtle mt-3 text-sm">or drag files anywhere onto this page</p>
    </div>
  );
}
