import { describe, expect, it } from "vitest";

import {
  DEFAULT_LIST_STATE,
  MAX_SEARCH_LENGTH,
  MAX_TAG_FILTERS,
  documentsHref,
  isNarrowed,
  listStateToFilters,
  listStateToSearch,
  parseListState,
  toggleTag,
  withoutFilters,
  type DocumentListState,
} from "@/features/documents/lib/list-params";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const C = "33333333-3333-4333-8333-333333333333";

const parse = (query: string) => parseListState(new URLSearchParams(query));

describe("parseListState", () => {
  it("is the default for an empty address", () => {
    expect(parse("")).toEqual(DEFAULT_LIST_STATE);
  });

  it("reads every parameter", () => {
    expect(
      parse(`q=budget&sort=title_asc&status=ready&folder=${A}&tag=${B}&tag=${C}&archive=archived`),
    ).toEqual({
      q: "budget",
      sort: "title_asc",
      status: "ready",
      folder: { kind: "folder", id: A },
      tags: [B, C],
      archive: "archived",
    });
  });

  it("reads folder=none as the unfiled scope", () => {
    expect(parse("folder=none").folder).toEqual({ kind: "unfiled" });
  });

  describe("an address anyone can edit never reaches the API as an invalid value", () => {
    it.each([
      ["an unknown sort", "sort=sideways", "sort", "created_desc"],
      ["an unknown status", "status=on-fire", "status", undefined],
      ["an unknown archive value", "archive=everything", "archive", "active"],
    ])("%s falls back to the default", (_label, query, key, expected) => {
      expect(parse(query)[key as keyof DocumentListState]).toEqual(expected);
    });

    it("drops a folder that is not a UUID", () => {
      expect(parse("folder=../../etc/passwd").folder).toEqual({ kind: "all" });
      expect(parse("folder=42").folder).toEqual({ kind: "all" });
    });

    it("drops tags that are not UUIDs and keeps the valid ones", () => {
      expect(parse(`tag=nope&tag=${A}&tag=%3Cscript%3E`).tags).toEqual([A]);
    });

    it("collapses repeated tags", () => {
      expect(parse(`tag=${A}&tag=${A}`).tags).toEqual([A]);
    });

    it("caps the number of tags the API would refuse", () => {
      const ids = Array.from(
        { length: 9 },
        (_, index) => `${String(index).repeat(8)}-1111-4111-8111-111111111111`,
      );
      expect(parse(ids.map((id) => `tag=${id}`).join("&")).tags).toHaveLength(MAX_TAG_FILTERS);
    });

    it("truncates an overlong search rather than sending a request the API would refuse", () => {
      expect(parse(`q=${"x".repeat(500)}`).q).toHaveLength(MAX_SEARCH_LENGTH);
    });
  });
});

describe("listStateToSearch", () => {
  it("writes nothing for the default, so an untouched list has a clean URL", () => {
    expect(listStateToSearch(DEFAULT_LIST_STATE).toString()).toBe("");
  });

  it("round-trips any state", () => {
    const state: DocumentListState = {
      q: "annual report",
      sort: "updated_desc",
      status: "failed",
      folder: { kind: "folder", id: A },
      tags: [B, C],
      archive: "archived",
    };
    expect(parseListState(listStateToSearch(state))).toEqual(state);
  });

  it("round-trips the unfiled scope", () => {
    const state = { ...DEFAULT_LIST_STATE, folder: { kind: "unfiled" } as const };
    expect(parseListState(listStateToSearch(state))).toEqual(state);
  });

  it("trims the search text and omits it when blank", () => {
    expect(listStateToSearch({ ...DEFAULT_LIST_STATE, q: "   " }).has("q")).toBe(false);
    expect(listStateToSearch({ ...DEFAULT_LIST_STATE, q: "  hi " }).get("q")).toBe("hi");
  });

  it("keeps characters that matter in the URL intact", () => {
    const state = { ...DEFAULT_LIST_STATE, q: "Q3 & Q4 100%" };
    expect(parse(listStateToSearch(state).toString()).q).toBe("Q3 & Q4 100%");
  });
});

describe("listStateToFilters", () => {
  it("sends nothing for the default", () => {
    expect(listStateToFilters(DEFAULT_LIST_STATE)).toEqual({});
  });

  it("maps scope to exactly one of folderId / unfiled", () => {
    expect(
      listStateToFilters({ ...DEFAULT_LIST_STATE, folder: { kind: "folder", id: A } }),
    ).toEqual({ folderId: A });
    expect(listStateToFilters({ ...DEFAULT_LIST_STATE, folder: { kind: "unfiled" } })).toEqual({
      unfiled: true,
    });
  });

  it("maps the rest", () => {
    expect(
      listStateToFilters({
        q: " budget ",
        sort: "title_desc",
        status: "ready",
        folder: { kind: "all" },
        tags: [A, B],
        archive: "archived",
      }),
    ).toEqual({
      q: "budget",
      sort: "title_desc",
      status: "ready",
      tagIds: [A, B],
      archive: "archived",
    });
  });
});

describe("isNarrowed", () => {
  it("is false for the default and for a sort alone", () => {
    expect(isNarrowed(DEFAULT_LIST_STATE)).toBe(false);
    expect(isNarrowed({ ...DEFAULT_LIST_STATE, sort: "title_asc" })).toBe(false);
  });

  it.each<[string, Partial<DocumentListState>]>([
    ["search text", { q: "x" }],
    ["a status", { status: "ready" }],
    ["a folder", { folder: { kind: "folder", id: A } }],
    ["unfiled", { folder: { kind: "unfiled" } }],
    ["a tag", { tags: [A] }],
    ["the archive", { archive: "archived" }],
  ])("is true for %s", (_label, change) => {
    expect(isNarrowed({ ...DEFAULT_LIST_STATE, ...change })).toBe(true);
  });

  it("does not count whitespace-only search text", () => {
    expect(isNarrowed({ ...DEFAULT_LIST_STATE, q: "   " })).toBe(false);
  });
});

describe("editing the state", () => {
  it("adds and removes a tag", () => {
    const added = toggleTag(DEFAULT_LIST_STATE, A);
    expect(added.tags).toEqual([A]);
    expect(toggleTag(added, A).tags).toEqual([]);
  });

  it("refuses a sixth tag instead of building a request the API rejects", () => {
    let state = DEFAULT_LIST_STATE;
    for (let index = 0; index < MAX_TAG_FILTERS; index += 1) {
      state = toggleTag(state, `${index}`.repeat(8) + "-1111-4111-8111-111111111111");
    }
    const sixth = "99999999-9999-4999-8999-999999999999";
    expect(toggleTag(state, sixth)).toBe(state);
  });

  it("still lets a tag be removed at the limit", () => {
    const ids = Array.from({ length: MAX_TAG_FILTERS }, (_, index) => `t${index}`);
    const full = { ...DEFAULT_LIST_STATE, tags: ids };
    expect(toggleTag(full, "t0").tags).toHaveLength(MAX_TAG_FILTERS - 1);
  });

  it("clears filters but keeps the sort and the folder and archive scope", () => {
    const state: DocumentListState = {
      q: "x",
      sort: "title_asc",
      status: "ready",
      folder: { kind: "folder", id: A },
      tags: [B],
      archive: "archived",
    };
    expect(withoutFilters(state)).toEqual({
      ...state,
      q: "",
      status: undefined,
      tags: [],
    });
  });
});

describe("documentsHref", () => {
  it("is the bare route for the default", () => {
    expect(documentsHref("ws-1", DEFAULT_LIST_STATE)).toBe("/workspaces/ws-1/documents");
  });

  it("appends the query", () => {
    expect(documentsHref("ws-1", { ...DEFAULT_LIST_STATE, folder: { kind: "unfiled" } })).toBe(
      "/workspaces/ws-1/documents?folder=none",
    );
  });
});
