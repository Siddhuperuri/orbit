import { describe, expect, it } from "vitest";

import {
  FALLBACK_POLICY,
  acceptAttribute,
  describeFormats,
  extensionOf,
  validateFile,
  type UploadPolicy,
} from "@/features/documents/upload/validate";

const MB = 1024 * 1024;
const POLICY: UploadPolicy = {
  maxBytes: 50 * MB,
  extensions: [".markdown", ".md", ".pdf", ".txt"],
};

const file = (name: string, size = 1024) => ({ name, size });

describe("extensionOf", () => {
  it.each([
    ["report.pdf", ".pdf"],
    ["REPORT.PDF", ".pdf"],
    ["archive.tar.gz", ".gz"],
    ["notes.MD", ".md"],
  ])("%s -> %s", (name, expected) => {
    expect(extensionOf(name)).toBe(expected);
  });

  it("is empty for a name with no extension, or only a leading dot", () => {
    expect(extensionOf("Makefile")).toBe("");
    expect(extensionOf(".gitignore")).toBe("");
    expect(extensionOf("trailing.")).toBe(".");
  });
});

describe("validateFile", () => {
  it("accepts a supported file within the limit", () => {
    expect(validateFile(file("a.pdf", 10 * MB), POLICY)).toBeNull();
  });

  it.each([".pdf", ".md", ".markdown", ".txt"])("accepts %s regardless of case", (extension) => {
    expect(validateFile(file(`x${extension.toUpperCase()}`), POLICY)).toBeNull();
  });

  it("rejects an unsupported type and names what is accepted", () => {
    const rejection = validateFile(file("budget.xlsx"), POLICY);
    expect(rejection?.code).toBe("unsupported-type");
    expect(rejection?.message).toBe(
      "budget.xlsx isn't a supported type. ORBIT accepts Markdown, PDF, and plain text.",
    );
  });

  it("rejects a file with no extension as unsupported", () => {
    expect(validateFile(file("README"), POLICY)?.code).toBe("unsupported-type");
  });

  it("rejects an empty file", () => {
    const rejection = validateFile(file("blank.txt", 0), POLICY);
    expect(rejection).toEqual({ code: "empty", message: "blank.txt is empty." });
  });

  it("rejects a file over the limit and states both sizes", () => {
    const rejection = validateFile(file("huge.pdf", 80 * MB), POLICY);
    expect(rejection?.code).toBe("too-large");
    expect(rejection?.message).toBe("huge.pdf is 80 MB; the limit is 50 MB.");
  });

  it("accepts a file of exactly the limit", () => {
    expect(validateFile(file("edge.pdf", 50 * MB), POLICY)).toBeNull();
    expect(validateFile(file("edge.pdf", 50 * MB + 1), POLICY)?.code).toBe("too-large");
  });

  it("checks the type before the size, so the more fundamental problem is reported", () => {
    expect(validateFile(file("huge.exe", 900 * MB), POLICY)?.code).toBe("unsupported-type");
  });

  it("does not check size when the limit is unknown -- the server still does", () => {
    expect(validateFile(file("big.pdf", 900 * MB), FALLBACK_POLICY)).toBeNull();
  });

  it("uses the deployment's own extensions, not a hardcoded list", () => {
    const pdfOnly: UploadPolicy = { maxBytes: null, extensions: [".pdf"] };
    expect(validateFile(file("a.md"), pdfOnly)?.code).toBe("unsupported-type");
    expect(validateFile(file("a.pdf"), pdfOnly)).toBeNull();
  });
});

describe("describing the accepted formats", () => {
  it("names each format once, in plain words", () => {
    // `.md` and `.markdown` are one format.
    expect(describeFormats(POLICY)).toBe("Markdown, PDF, and plain text");
  });

  it("reads sensibly for one or two formats", () => {
    expect(describeFormats({ maxBytes: null, extensions: [".pdf"] })).toBe("PDF");
    expect(describeFormats({ maxBytes: null, extensions: [".md", ".pdf"] })).toBe(
      "Markdown, and PDF",
    );
  });

  it("falls back to the raw extension for a format it has no name for", () => {
    expect(describeFormats({ maxBytes: null, extensions: [".docx"] })).toBe(".docx");
  });

  it("builds the file input's accept attribute from the policy", () => {
    expect(acceptAttribute(POLICY)).toBe(".markdown,.md,.pdf,.txt");
  });
});
