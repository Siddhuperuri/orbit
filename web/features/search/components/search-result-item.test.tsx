import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SearchResultItem } from "@/features/search/components/search-result-item";
import type { SearchResult } from "@/features/search/types";
import { renderInWorkspace, WORKSPACE_ID } from "@/test/render";

const DOCUMENT_ID = "11111111-1111-4111-8111-111111111111";

function makeResult(overrides: Partial<SearchResult> = {}): SearchResult {
  return {
    rank: 1,
    matched_by: "both",
    document: {
      id: DOCUMENT_ID,
      title: "Employee handbook",
      content_type: "application/pdf",
      updated_at: "2026-09-18T12:00:00Z",
    },
    version: { id: "v1", version_number: 3 },
    chunk: { id: "c1", ordinal: 7, text: "Parental leave is sixteen weeks at full pay." },
    location: {
      page_from: 12,
      page_to: 12,
      heading_path: "Leave > Parental",
      char_start: 100,
      char_end: 143,
    },
    // Diagnostics the API returns and the UI must not put on screen.
    relevance: {
      score: 0.016_393,
      lexical: { rank: 2, score: 0.412_8 },
      semantic: { rank: 1, score: 0.847_2 },
    },
    ...overrides,
  };
}

function renderResult(overrides: Partial<SearchResult> = {}, onAsk?: (r: SearchResult) => void) {
  return renderInWorkspace(
    <ol>
      <SearchResultItem
        result={makeResult(overrides)}
        query="parental leave"
        workspaceId={WORKSPACE_ID}
        mode="hybrid"
        onAsk={onAsk}
      />
    </ol>,
  );
}

describe("SearchResultItem", () => {
  it("shows what a reader needs to judge the result", () => {
    const { container } = renderResult();

    expect(screen.getByRole("link", { name: "Employee handbook" })).toBeInTheDocument();
    // Read from the rendered text: the excerpt is split across <mark> elements
    // by the query highlighter, so it is not one text node.
    expect(container.textContent).toContain("Parental leave is sixteen weeks at full pay.");
    expect(container.textContent).toContain("PDF");
    expect(container.textContent).toContain("Page 12");
    expect(container.textContent).toContain("Leave > Parental");
    expect(container.textContent).toContain("Updated");
  });

  it("opens the document at the cited passage, not at its top", () => {
    renderResult();

    const href = `/workspaces/${WORKSPACE_ID}/documents/${DOCUMENT_ID}?passage=7&v=3`;
    expect(screen.getByRole("link", { name: "Employee handbook" })).toHaveAttribute("href", href);
    expect(screen.getByRole("link", { name: /Open at this passage/ })).toHaveAttribute(
      "href",
      href,
    );
  });

  /**
   * The product requirement, pinned: ranking internals are diagnostics, not
   * something a reader can act on. A fused RRF score is meaningless outside its
   * own response, and the retrievers' native scores are on different scales.
   */
  it("shows no raw ranking numbers anywhere", () => {
    const { container } = renderResult();

    const text = container.textContent ?? "";
    expect(text).not.toMatch(/0\.016/);
    expect(text).not.toMatch(/0\.41/);
    expect(text).not.toMatch(/0\.84/);
    expect(text).not.toMatch(/#\d/);
    expect(screen.queryByText(/combined score/i)).not.toBeInTheDocument();
  });

  it("explains the match in terms the reader can act on", async () => {
    const { user } = renderResult({ matched_by: "semantic" });

    expect(screen.getByText("Meaning match")).toBeInTheDocument();
    await user.click(screen.getByText("Why this result"));
    expect(screen.getByText(/doesn't use your exact words/)).toBeInTheDocument();
  });

  it("drops the match badge in a single-retriever search, where it says nothing", () => {
    renderInWorkspace(
      <ol>
        <SearchResultItem
          result={makeResult()}
          query="parental leave"
          workspaceId={WORKSPACE_ID}
          mode="lexical"
        />
      </ol>,
    );

    expect(screen.queryByText("Strong match")).not.toBeInTheDocument();
    expect(screen.queryByText("Why this result")).not.toBeInTheDocument();
  });

  it("offers to ask about the document only when the caller may", async () => {
    const onAsk = vi.fn();
    const { user } = renderResult({}, onAsk);

    await user.click(screen.getByRole("button", { name: /Ask about this document/ }));
    expect(onAsk).toHaveBeenCalledOnce();

    renderResult();
    expect(screen.queryAllByRole("button", { name: /Ask about this document/ })).toHaveLength(1);
  });

  it("clamps a long passage but keeps all of it in the document", async () => {
    const long = "Parental leave detail. ".repeat(40);
    const { user } = renderResult({ chunk: { id: "c1", ordinal: 7, text: long } });

    const toggle = screen.getByRole("button", { name: "Show full passage" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(screen.getByRole("button", { name: "Show less" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("omits the location line's page when the document has no pages", () => {
    const { container } = renderResult({
      location: {
        page_from: null,
        page_to: null,
        heading_path: "Leave",
        char_start: 0,
        char_end: 10,
      },
    });

    expect(container.textContent).toContain("Leave");
    expect(container.textContent).not.toContain("Page ");
  });
});
