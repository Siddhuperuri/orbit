import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SourcesList } from "@/features/chat/components/sources-list";
import type { Citation } from "@/features/chat/types";
import { navigation } from "@/test/navigation";

/**
 * What an answer's sources show, and where they lead.
 *
 * This is the component the product's central promise rests on: every field it
 * renders is copied from the retrieved chunk's own database record, never from
 * model output (ADR-0006). So the assertions here are mostly about *fidelity* --
 * the snippet is verbatim, the link points at the cited version and passage, and
 * a citation missing its location degrades rather than inventing one.
 */

const WORKSPACE_ID = "ws-1";
const MESSAGE_ID = "msg-1";

function makeCitation(overrides: Partial<Citation> = {}): Citation {
  return {
    handle: "s1",
    ordinal: 1,
    snippet: "A lockout tag is removed by the technician who applied it.",
    document: { id: "doc-1", title: "Field Operations Handbook" },
    chunk: { id: "chunk-1", ordinal: 7 },
    version: { id: "ver-1", version_number: 3 },
    location: {
      char_start: 120,
      char_end: 240,
      heading_path: "Section 2 › Safety Lockout",
      page_from: 2,
      page_to: 2,
    },
    ...overrides,
  } as Citation;
}

function renderSources(
  citations: Citation[],
  { openHandle = null as string | null, onToggle = vi.fn() } = {},
) {
  navigation.reset("/");
  const result = render(
    <SourcesList
      messageId={MESSAGE_ID}
      workspaceId={WORKSPACE_ID}
      citations={citations}
      openHandle={openHandle}
      onToggle={onToggle}
    />,
  );
  return { user: userEvent.setup(), onToggle, ...result };
}

describe("SourcesList", () => {
  it("renders nothing at all when there are no citations", () => {
    const { container } = renderSources([]);
    // Not an empty "Sources" heading: a heading with nothing under it reads as
    // sources that failed to load, which is a different and alarming claim.
    expect(container).toBeEmptyDOMElement();
  });

  it("lists each source by its handle, title, and location", () => {
    renderSources([
      makeCitation(),
      makeCitation({
        handle: "s2",
        ordinal: 2,
        document: { id: "doc-2", title: "Incident Register" },
        location: {
          char_start: null,
          char_end: null,
          heading_path: null,
          page_from: 9,
          page_to: 11,
        },
      }),
    ]);

    expect(screen.getByRole("heading", { name: "Sources" })).toBeInTheDocument();
    // Handles are shown uppercased, matching the markers in the answer text.
    expect(screen.getByText("S1")).toBeInTheDocument();
    expect(screen.getByText("S2")).toBeInTheDocument();
    expect(screen.getByText("Field Operations Handbook")).toBeInTheDocument();
    expect(screen.getByText("Incident Register")).toBeInTheDocument();
  });

  it("keeps a source collapsed until it is opened", async () => {
    const snippet = "A lockout tag is removed by the technician who applied it.";
    const { onToggle, user } = renderSources([makeCitation({ snippet })]);

    const disclosure = screen.getByRole("button", { name: /Field Operations Handbook/ });
    expect(disclosure).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(snippet)).not.toBeInTheDocument();

    await user.click(disclosure);
    // The component does not own which one is open -- the thread does, so that a
    // marker in the answer can open the matching source. It only reports intent.
    expect(onToggle).toHaveBeenCalledWith("S1");
  });

  it("shows the passage verbatim once open, and links to it in the document", () => {
    const snippet = "A lockout tag is removed by the technician who applied it.";
    renderSources([makeCitation({ snippet })], { openHandle: "S1" });

    const disclosure = screen.getByRole("button", { name: /Field Operations Handbook/ });
    expect(disclosure).toHaveAttribute("aria-expanded", "true");

    // Verbatim, and marked up as a quotation rather than as the app's own prose.
    const quote = screen.getByText(snippet);
    expect(quote.closest("blockquote")).not.toBeNull();

    // The link carries the passage *and* the version it was cited from, so a
    // citation against text that has since been replaced can say so rather than
    // silently pointing at something else.
    const link = screen.getByRole("link", { name: "Open at this passage" });
    expect(link).toHaveAttribute(
      "href",
      `/workspaces/${WORKSPACE_ID}/documents/doc-1?passage=7&v=3`,
    );
  });

  it("degrades to opening the document when the passage is unknown", () => {
    renderSources(
      [
        makeCitation({
          chunk: { id: null, ordinal: null },
          version: { id: null, version_number: null },
        }),
      ],
      { openHandle: "S1" },
    );

    // The wording changes with the promise: without an ordinal it cannot claim
    // to land on the passage, so it does not.
    const link = screen.getByRole("link", { name: "Open document" });
    expect(link).toHaveAttribute("href", `/workspaces/${WORKSPACE_ID}/documents/doc-1`);
  });

  it("gives each disclosure its own panel id so the markup stays unambiguous", () => {
    renderSources([makeCitation(), makeCitation({ handle: "s2", ordinal: 2 })], {
      openHandle: "S2",
    });

    const items = screen.getAllByRole("button");
    const controls = items.map((item) => item.getAttribute("aria-controls"));
    // Two identical ids would make a screen reader announce the wrong passage.
    expect(new Set(controls).size).toBe(controls.length);
    expect(controls.every((id) => id?.includes(MESSAGE_ID))).toBe(true);
  });

  it("renders a snippet containing markup as text, not as markup", () => {
    // A document can legitimately contain angle brackets. The snippet is
    // verbatim source text and must never be interpreted.
    const hostile = '<img src=x onerror="alert(1)"> and <b>bold</b>';
    renderSources([makeCitation({ snippet: hostile })], { openHandle: "S1" });

    const quote = screen.getByText(hostile);
    expect(within(quote).queryByRole("img")).not.toBeInTheDocument();
    expect(quote.querySelector("b")).toBeNull();
  });
});
