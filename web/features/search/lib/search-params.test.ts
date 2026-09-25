import { describe, expect, it } from "vitest";

import {
  DEFAULT_SEARCH_STATE,
  MAX_PAGE,
  isNarrowed,
  parseSearchState,
  refine,
  searchHref,
  searchStateToQuery,
  toggleTag,
  toggleType,
  withoutFilters,
  type SearchState,
} from "@/features/search/lib/search-params";
import { MAX_TAG_FILTERS } from "@/features/search/types";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const C = "33333333-3333-4333-8333-333333333333";

const parse = (query: string) => parseSearchState(new URLSearchParams(query));
const state = (overrides: Partial<SearchState> = {}): SearchState => ({
  ...DEFAULT_SEARCH_STATE,
  ...overrides,
});

describe("parseSearchState", () => {
  it("reads the query, mode, filters, and page", () => {
    const parsed = parse(
      `q=parental+leave&mode=lexical&folder=${A}&tag=${B}&type=text%2Fplain&page=2`,
    );

    expect(parsed).toEqual({
      query: "parental leave",
      mode: "lexical",
      folder: { kind: "folder", id: A },
      tags: [B],
      types: ["text/plain"],
      documentId: undefined,
      page: 2,
    });
  });

  it("defaults everything an empty address leaves unsaid", () => {
    expect(parse("")).toEqual(DEFAULT_SEARCH_STATE);
  });

  describe("a hand-edited address", () => {
    it("falls back rather than passing an unknown mode to the API", () => {
      expect(parse("q=x&mode=telepathy").mode).toBe("hybrid");
    });

    it("drops ids that are not UUIDs", () => {
      expect(parse("q=x&folder=../../etc&tag=drop-table&doc=1").folder).toEqual({ kind: "all" });
      expect(parse("q=x&tag=drop-table").tags).toEqual([]);
      expect(parse("q=x&doc=1").documentId).toBeUndefined();
    });

    it("drops a content type ORBIT cannot store", () => {
      expect(parse("q=x&type=application%2Fzip&type=text%2Fplain").types).toEqual(["text/plain"]);
    });

    it("caps the tag filter at the API's limit", () => {
      const many = Array.from({ length: 9 }, (_, i) => `tag=${A.slice(0, -1)}${i}`).join("&");
      expect(parse(`q=x&${many}`).tags.length).toBeLessThanOrEqual(MAX_TAG_FILTERS);
    });

    it("clamps a page past the API's offset ceiling instead of erroring", () => {
      expect(parse("q=x&page=99999").page).toBe(MAX_PAGE);
      expect(parse("q=x&page=-4").page).toBe(0);
      expect(parse("q=x&page=two").page).toBe(0);
    });

    it("deduplicates repeated filters", () => {
      expect(parse(`q=x&tag=${B}&tag=${B}`).tags).toEqual([B]);
    });
  });

  it("round-trips through the query string", () => {
    const original = state({
      query: "parental leave",
      mode: "semantic",
      folder: { kind: "unfiled" },
      tags: [B, C],
      types: ["application/pdf"],
      documentId: A,
      page: 3,
    });
    expect(parse(searchStateToQuery(original).toString())).toEqual(original);
  });
});

describe("searchStateToQuery", () => {
  it("omits every default, so a plain search has a clean URL", () => {
    expect(searchStateToQuery(state({ query: "leave" })).toString()).toBe("q=leave");
  });

  it("leaves the query out entirely when there is none", () => {
    expect(searchHref("w1", DEFAULT_SEARCH_STATE)).toBe("/workspaces/w1/search");
  });
});

describe("isNarrowed", () => {
  it("is false for a search of the whole workspace", () => {
    expect(isNarrowed(state({ query: "leave", mode: "lexical" }))).toBe(false);
  });

  it.each([
    ["a folder", state({ folder: { kind: "folder", id: A } })],
    ["unfiled", state({ folder: { kind: "unfiled" } })],
    ["a tag", state({ tags: [B] })],
    ["a type", state({ types: ["text/plain"] })],
    ["one document", state({ documentId: A })],
  ])("is true when narrowed by %s", (_label, narrowed) => {
    expect(isNarrowed(narrowed)).toBe(true);
  });
});

describe("refining a search", () => {
  it("returns to page one, so a refinement never lands mid-ranking", () => {
    expect(refine(state({ page: 4 }), { mode: "lexical" }).page).toBe(0);
    expect(toggleTag(state({ page: 4 }), B).page).toBe(0);
    expect(toggleType(state({ page: 4 }), "text/plain").page).toBe(0);
    expect(withoutFilters(state({ page: 4, tags: [B] })).page).toBe(0);
  });

  it("keeps the query and mode when filters are cleared", () => {
    const cleared = withoutFilters(
      state({ query: "leave", mode: "semantic", tags: [B], types: ["text/plain"], documentId: A }),
    );
    expect(cleared.query).toBe("leave");
    expect(cleared.mode).toBe("semantic");
    expect(isNarrowed(cleared)).toBe(false);
  });

  it("toggles a tag off as well as on", () => {
    expect(toggleTag(state({ tags: [B] }), B).tags).toEqual([]);
    expect(toggleTag(state(), B).tags).toEqual([B]);
  });

  it("refuses to add a tag past the API's limit rather than sending a 422", () => {
    const full = state({ tags: Array.from({ length: MAX_TAG_FILTERS }, (_, i) => `${i}`) });
    expect(toggleTag(full, B)).toBe(full);
  });
});
