import type { DocumentVersion } from "@/features/documents/types";

export type FileKind = "pdf" | "markdown" | "text" | "other";

const LABELS: Record<FileKind, string> = {
  pdf: "PDF",
  markdown: "Markdown",
  text: "Text",
  other: "File",
};

/**
 * What kind of file a version is, for display: from the type the server sniffed
 * from the bytes, falling back to the filename's extension.
 */
export function fileKindOf(
  version: Pick<DocumentVersion, "content_type" | "original_filename"> | null | undefined,
): FileKind {
  if (!version) return "other";
  const type = version.content_type.toLowerCase();
  const extension = version.original_filename.toLowerCase().split(".").pop() ?? "";
  if (type === "application/pdf" || extension === "pdf") return "pdf";
  if (type === "text/markdown" || extension === "md" || extension === "markdown") {
    return "markdown";
  }
  if (type.startsWith("text/") || extension === "txt") return "text";
  return "other";
}

export function fileKindLabel(kind: FileKind): string {
  return LABELS[kind];
}
