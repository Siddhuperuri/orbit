import { describe, expect, it } from "vitest";

import {
  describeCitationLocation,
  groundingNotice,
  handleFromHref,
  linkCitations,
} from "@/features/chat/lib/citations";
import type { Citation } from "@/features/chat/types";

function citation(handle: string): Citation {
  return {
    handle,
    ordinal: 1,
    document: { id: "d", title: "Doc" },
    version: { id: "v", version_number: 1 },
    chunk: { id: "c", ordinal: 0 },
    location: {
      page_from: null,
      page_to: null,
      heading_path: null,
      char_start: null,
      char_end: null,
    },
    snippet: "",
  };
}

describe("linkCitations", () => {
  it("links handles that resolved to a citation", () => {
    expect(
      linkCitations("Revenue rose [S1] and costs fell [S2].", [citation("S1"), citation("S2")]),
    ).toBe("Revenue rose [S1](#cite-S1) and costs fell [S2](#cite-S2).");
  });

  it("links adjacent handles", () => {
    expect(linkCitations("See [S1][S3].", [citation("S1"), citation("S3")])).toBe(
      "See [S1](#cite-S1)[S3](#cite-S3).",
    );
  });

  it("leaves a handle with no citation as plain text -- never presents it as a source", () => {
    expect(linkCitations("Claimed [S9].", [citation("S1")])).toBe("Claimed [S9].");
  });

  it("does nothing when there are no citations at all", () => {
    expect(linkCitations("Text [S1].", [])).toBe("Text [S1].");
  });

  it("is case-insensitive about the handle the model wrote", () => {
    expect(linkCitations("x [s1]", [citation("S1")])).toBe("x [S1](#cite-S1)");
  });
});

describe("handleFromHref", () => {
  it("extracts the handle from a citation link and ignores every other link", () => {
    expect(handleFromHref("#cite-S2")).toBe("S2");
    expect(handleFromHref("https://example.com")).toBeNull();
    expect(handleFromHref(undefined)).toBeNull();
  });
});

describe("describeCitationLocation", () => {
  it("formats a page range and heading trail", () => {
    expect(
      describeCitationLocation({
        page_from: 3,
        page_to: 4,
        heading_path: "Results > Q3",
        char_start: 0,
        char_end: 1,
      }),
    ).toBe("Pages 3–4 · Results > Q3");
  });
  it("collapses a single page and copes with nothing", () => {
    expect(
      describeCitationLocation({
        page_from: 2,
        page_to: 2,
        heading_path: null,
        char_start: null,
        char_end: null,
      }),
    ).toBe("Page 2");
    expect(
      describeCitationLocation({
        page_from: null,
        page_to: null,
        heading_path: null,
        char_start: null,
        char_end: null,
      }),
    ).toBeNull();
  });
});

describe("groundingNotice", () => {
  it("says nothing for a grounded answer", () => {
    expect(groundingNotice("grounded")).toBeNull();
    expect(groundingNotice(null)).toBeNull();
  });

  it("marks every weaker grounding, so an uncited answer never looks like a cited one", () => {
    for (const grounding of ["uncited", "insufficient_evidence", "no_evidence"] as const) {
      expect(groundingNotice(grounding)).not.toBeNull();
    }
  });

  it("treats an uncited answer as a warning", () => {
    expect(groundingNotice("uncited")?.tone).toBe("warning");
  });
});
