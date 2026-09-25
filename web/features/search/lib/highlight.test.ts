import { describe, expect, it } from "vitest";

import { highlightSegments, queryTerms } from "@/features/search/lib/highlight";

describe("queryTerms", () => {
  it("lowercases, trims punctuation, drops one-letter words, and de-duplicates", () => {
    expect(queryTerms('The "Quarterly," budget (Q3) a budget')).toEqual([
      "the",
      "quarterly",
      "budget",
      "q3",
    ]);
  });

  it("caps the number of terms so a pasted paragraph cannot build a huge pattern", () => {
    const paragraph = Array.from({ length: 200 }, (_, index) => `word${index}`).join(" ");
    expect(queryTerms(paragraph).length).toBeLessThanOrEqual(12);
  });
});

describe("highlightSegments", () => {
  it("marks matches case-insensitively and preserves the original casing", () => {
    expect(highlightSegments("Budget for the budget team", "budget")).toEqual([
      { text: "Budget", match: true },
      { text: " for the ", match: false },
      { text: "budget", match: true },
      { text: " team", match: false },
    ]);
  });

  it("prefers the longer of two overlapping terms", () => {
    const segments = highlightSegments("database design", "data database");
    expect(segments[0]).toEqual({ text: "database", match: true });
  });

  it("returns the text untouched when nothing matches or the query is empty", () => {
    expect(highlightSegments("nothing here", "zebra")).toEqual([
      { text: "nothing here", match: false },
    ]);
    expect(highlightSegments("some text", "  ")).toEqual([{ text: "some text", match: false }]);
  });

  it("treats regex metacharacters in the query as literal text, not a pattern", () => {
    expect(() => highlightSegments("a.b (c)", "a.b (c) [x] .* +?")).not.toThrow();
    expect(highlightSegments("cost is $5.00", "5.00")).toContainEqual({
      text: "5.00",
      match: true,
    });
    // "." must not match any character.
    expect(highlightSegments("abc", "a.c").every((segment) => !segment.match)).toBe(true);
  });

  it("never turns markup in the source text into anything but text", () => {
    const hostile = '<img src=x onerror="alert(1)"> budget';
    const segments = highlightSegments(hostile, "budget");
    expect(segments.map((segment) => segment.text).join("")).toBe(hostile);
  });

  it("handles non-ASCII words", () => {
    expect(highlightSegments("Café münchen", "münchen")).toContainEqual({
      text: "münchen",
      match: true,
    });
  });
});
