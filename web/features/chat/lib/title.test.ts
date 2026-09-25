import { describe, expect, it } from "vitest";

import { titleFromQuestion } from "@/features/chat/lib/title";

describe("titleFromQuestion", () => {
  it("uses a short question as is", () => {
    expect(titleFromQuestion("How much did the budget increase?")).toBe(
      "How much did the budget increase?",
    );
  });

  it("uses only the first non-empty line and collapses whitespace", () => {
    expect(titleFromQuestion("\n\n  What   changed\tlast   quarter?\nAlso: costs")).toBe(
      "What changed last quarter?",
    );
  });

  it("cuts a long question on a word boundary with an ellipsis", () => {
    const title = titleFromQuestion(
      "Summarise every risk mentioned across the vendor contracts and the observability platform renewal terms",
    );
    expect(title).toBeDefined();
    expect(title!.length).toBeLessThanOrEqual(73);
    expect(title!.endsWith("…")).toBe(true);
    expect(title).not.toMatch(/\s…$/);
  });

  it("never exceeds the length limit even for an unbroken string", () => {
    expect(titleFromQuestion("a".repeat(500))!.length).toBeLessThanOrEqual(73);
  });

  it("returns undefined for nothing, so the server's default title is used", () => {
    expect(titleFromQuestion("   \n  ")).toBeUndefined();
  });
});
