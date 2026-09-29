import { describe, expect, it } from "vitest";

import { fileKindLabel, fileKindOf } from "@/features/documents/lib/file-kind";

const version = (content_type: string, original_filename: string) => ({
  content_type,
  original_filename,
});

describe("fileKindOf", () => {
  it("trusts the sniffed content type first", () => {
    expect(fileKindOf(version("application/pdf", "notes.txt"))).toBe("pdf");
    expect(fileKindOf(version("text/markdown", "readme"))).toBe("markdown");
    expect(fileKindOf(version("text/plain", "log"))).toBe("text");
  });

  it("falls back to the extension, case-insensitively", () => {
    expect(fileKindOf(version("application/octet-stream", "Guide.PDF"))).toBe("pdf");
    expect(fileKindOf(version("application/octet-stream", "notes.markdown"))).toBe("markdown");
    expect(fileKindOf(version("application/octet-stream", "a.txt"))).toBe("text");
  });

  it("calls anything else, and a missing version, a file", () => {
    expect(fileKindOf(version("application/zip", "archive.zip"))).toBe("other");
    expect(fileKindOf(null)).toBe("other");
    expect(fileKindLabel("other")).toBe("File");
  });
});
