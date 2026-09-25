import { describe, expect, it } from "vitest";

import { formatBytes, formatRelativeTime, initialsOf, pluralize } from "@/lib/utils/format";

describe("formatBytes", () => {
  it.each([
    [0, "0 B"],
    [1023, "1,023 B"],
    [1024, "1 KB"],
    [1536, "1.5 KB"],
    [52_428_800, "50 MB"],
    [1_073_741_824, "1 GB"],
  ])("%d -> %s", (input, expected) => {
    expect(formatBytes(input)).toBe(expected);
  });

  it("does not print nonsense for invalid input", () => {
    expect(formatBytes(-1)).toBe("—");
    expect(formatBytes(Number.NaN)).toBe("—");
  });
});

describe("formatRelativeTime", () => {
  const now = Date.parse("2026-09-19T12:00:00Z");
  it("says 'just now' for the last minute", () => {
    expect(formatRelativeTime("2026-09-19T11:59:40Z", now)).toBe("just now");
  });
  it("uses relative units within a month", () => {
    expect(formatRelativeTime("2026-09-19T10:00:00Z", now)).toMatch(/2 hr/);
    expect(formatRelativeTime("2026-09-18T12:00:00Z", now)).toBe("yesterday");
  });
  it("falls back to a date for old timestamps", () => {
    expect(formatRelativeTime("2025-01-05T12:00:00Z", now)).toMatch(/2025/);
  });
  it("survives garbage", () => {
    expect(formatRelativeTime("not a date", now)).toBe("—");
  });
});

describe("small helpers", () => {
  it("pluralises", () => {
    expect(pluralize(1, "document")).toBe("1 document");
    expect(pluralize(1200, "document")).toBe("1,200 documents");
  });
  it("derives initials without throwing", () => {
    expect(initialsOf("Ada Lovelace")).toBe("AL");
    expect(initialsOf("Plato")).toBe("P");
    expect(initialsOf("  ")).toBe("?");
  });
});
