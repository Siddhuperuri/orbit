"use client";

import { Upload } from "lucide-react";
import { useRef } from "react";

import { Button, type ButtonProps } from "@/components/ui/button";
import { useUploadPolicy } from "@/features/documents/upload/use-upload-policy";
import { useUploadQueue } from "@/features/documents/upload/upload-queue-provider";
import { acceptAttribute } from "@/features/documents/upload/validate";

/**
 * Opens the file picker and hands the choice to the upload queue, which checks each file
 * against the deployment's real limits and says why any is refused.
 *
 * `accept` only *filters the picker* -- a drag-and-drop bypasses it, and an extension
 * proves nothing about the bytes -- so nothing here decides what is allowed. The queue
 * validates, and the server has the last word.
 *
 * Callers render this only for roles that hold `document:create`.
 */
export function UploadButton({
  variant = "primary",
  size,
  label = "Upload documents",
  folderId,
}: {
  variant?: ButtonProps["variant"];
  size?: ButtonProps["size"];
  label?: string;
  /** Where the documents are filed. Omitted means the top level. */
  folderId?: string;
}) {
  const { add } = useUploadQueue();
  const policy = useUploadPolicy();
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={acceptAttribute(policy)}
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        data-testid="upload-input"
        onChange={(event) => {
          if (event.target.files?.length) add(Array.from(event.target.files), { folderId });
          // Choosing the same file twice must fire `change` again.
          event.target.value = "";
        }}
      />
      <Button variant={variant} size={size} onClick={() => inputRef.current?.click()}>
        <Upload aria-hidden="true" />
        {label}
      </Button>
    </>
  );
}
