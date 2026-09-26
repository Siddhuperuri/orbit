"use client";

import { CloudUpload } from "lucide-react";

import { OrbitIcon } from "@/components/feedback/empty-state";
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
        "viewfinder flex flex-col items-center border border-dashed px-6 py-20 text-center transition-colors duration-500",
        dragging ? "border-accent bg-accent-soft" : "border-line-strong bg-canvas",
      )}
    >
      <OrbitIcon icon={CloudUpload} className="mb-7" />
      <h2 className="text-fg font-serif text-3xl font-light tracking-tight">
        {dragging ? "Drop to upload" : (title ?? "Add your first documents")}
      </h2>
      <p className="text-fg-muted mt-2 max-w-md text-base">
        Upload {describeFormats(policy)} files
        {policy.maxBytes !== null ? `, up to ${formatBytes(policy.maxBytes)} each` : ""}. Once
        they&apos;re processed you can search them and ask questions answered from their contents.
      </p>
      {destination ? (
        <p className="text-fg-muted mt-1 text-sm">
          They&apos;ll be added to <span className="text-fg font-medium">{destination}</span>.
        </p>
      ) : null}
      <div className="mt-6">
        <UploadButton folderId={folderId} />
      </div>
      <p className="label-micro text-fg-subtle mt-4">or drag files anywhere onto this page</p>
    </div>
  );
}
