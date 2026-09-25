import { formatBytes } from "@/lib/utils/format";

/**
 * Checking a file *before* sending it.
 *
 * This is a courtesy, not a control: the server re-checks every rule here, and sniffs
 * the real type from the bytes, because an extension proves nothing. What checking on
 * the client buys is speed and kindness -- a 200 MB file of the wrong type is refused
 * in a millisecond with a reason, instead of after minutes of upload -- and the limits
 * it checks against are the deployment's own, fetched from `/meta`, so they cannot
 * drift from what the server enforces.
 */

export interface UploadPolicy {
  /** `null` when the server's limit is not known; the server still enforces it. */
  maxBytes: number | null;
  /** Lowercase, with the dot: `.pdf`. */
  extensions: readonly string[];
}

/** Used until (or unless) `/meta` answers. The size is unknown, so it is not checked here. */
export const FALLBACK_POLICY: UploadPolicy = {
  maxBytes: null,
  extensions: [".markdown", ".md", ".pdf", ".txt"],
};

export type RejectionCode = "unsupported-type" | "empty" | "too-large" | "duplicate";

export interface Rejection {
  code: RejectionCode;
  /** Written for the person who chose the file, and complete on its own. */
  message: string;
}

const FORMAT_NAMES: Record<string, string> = {
  ".pdf": "PDF",
  ".md": "Markdown",
  ".markdown": "Markdown",
  ".txt": "plain text",
};

export function extensionOf(fileName: string): string {
  const dot = fileName.lastIndexOf(".");
  // A leading dot (".gitignore") or no dot at all is not an extension.
  return dot > 0 ? fileName.slice(dot).toLowerCase() : "";
}

/** "PDF, Markdown, and plain text" -- the formats, named as people name them. */
export function describeFormats(policy: UploadPolicy): string {
  const names = [
    ...new Set(policy.extensions.map((extension) => FORMAT_NAMES[extension] ?? extension)),
  ];
  if (names.length <= 1) return names[0] ?? "no formats";
  return `${names.slice(0, -1).join(", ")}, and ${names[names.length - 1]}`;
}

/** The `accept` attribute for a file input: a hint that filters the picker, nothing more. */
export function acceptAttribute(policy: UploadPolicy): string {
  return policy.extensions.join(",");
}

export function validateFile(
  file: Pick<File, "name" | "size">,
  policy: UploadPolicy,
): Rejection | null {
  if (!policy.extensions.includes(extensionOf(file.name))) {
    return {
      code: "unsupported-type",
      message: `${file.name} isn't a supported type. ORBIT accepts ${describeFormats(policy)}.`,
    };
  }
  if (file.size === 0) {
    return { code: "empty", message: `${file.name} is empty.` };
  }
  if (policy.maxBytes !== null && file.size > policy.maxBytes) {
    return {
      code: "too-large",
      message: `${file.name} is ${formatBytes(file.size)}; the limit is ${formatBytes(policy.maxBytes)}.`,
    };
  }
  return null;
}
