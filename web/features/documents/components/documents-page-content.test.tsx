import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { documentApi } from "@/features/documents/api/endpoints";
import { DocumentsPageContent } from "@/features/documents/components/documents-page-content";
import { ApiError } from "@/lib/api/errors";
import { routes } from "@/lib/navigation";
import { makeDocumentIn } from "@/test/factories";
import { navigation } from "@/test/navigation";
import { WORKSPACE_ID, renderInWorkspace } from "@/test/render";

const page = (titles: string[], next: string | null = null) => ({
  items: titles.map((title, index) => makeDocumentIn("ready", { id: `${title}-${index}`, title })),
  has_more: next !== null,
  next_cursor: next,
});

function renderList(role: "member" | "viewer", url = routes.documents(WORKSPACE_ID)) {
  return renderInWorkspace(<DocumentsPageContent />, { role, url });
}

describe("the document list", () => {
  it("shows a loading state, then the documents", async () => {
    let resolve: (value: ReturnType<typeof page>) => void = () => undefined;
    vi.spyOn(documentApi, "list").mockReturnValue(new Promise((r) => (resolve = r)));
    renderList("member");

    expect(screen.getByText(/Loading documents/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();

    resolve(page(["Budget", "Handbook"]));
    const table = await screen.findByRole("table");
    expect(within(table).getByRole("link", { name: "Budget" })).toHaveAttribute(
      "href",
      routes.document(WORKSPACE_ID, "Budget-0"),
    );
    expect(within(table).getByRole("link", { name: "Handbook" })).toBeInTheDocument();
  });

  it("says why it failed and lets the user try again", async () => {
    const list = vi
      .spyOn(documentApi, "list")
      .mockRejectedValueOnce(
        new ApiError({
          code: "INTERNAL_ERROR",
          status: 503,
          message: "The service is unavailable.",
          requestId: "req-503",
        }),
      )
      .mockResolvedValue(page(["Budget"]));
    const { user } = renderList("member");

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /try again|retry/i }));

    expect(await screen.findByRole("link", { name: "Budget" })).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("pages with the server's cursor and says how many are loaded, not how many there are", async () => {
    const list = vi
      .spyOn(documentApi, "list")
      .mockResolvedValueOnce(page(["One", "Two"], "cursor-2"))
      .mockResolvedValueOnce(page(["Three"]));
    const { user } = renderList("member");

    expect(await screen.findByText("2 documents loaded")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByRole("link", { name: "Three" })).toBeInTheDocument();
    expect(list).toHaveBeenLastCalledWith(
      WORKSPACE_ID,
      expect.objectContaining({ cursor: "cursor-2" }),
      expect.anything(),
    );
    expect(screen.getByText("3 documents")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("is driven by the address: filters in the URL become server-side filters", async () => {
    const list = vi.spyOn(documentApi, "list").mockResolvedValue(page(["Budget"]));
    renderList("member", `${routes.documents(WORKSPACE_ID)}?q=budget&status=ready&sort=title_asc`);

    await screen.findByRole("link", { name: "Budget" });
    expect(list).toHaveBeenCalledWith(
      WORKSPACE_ID,
      expect.objectContaining({ q: "budget", status: "ready", sort: "title_asc" }),
      expect.anything(),
    );
    expect(screen.getByRole("searchbox", { name: /filter by title/i })).toHaveValue("budget");
    expect(screen.getByRole("combobox", { name: "Sort by" })).toHaveValue("title_asc");
  });

  it("writes a changed sort back to the address, and asks the server for that order", async () => {
    const list = vi.spyOn(documentApi, "list").mockResolvedValue(page(["Budget"]));
    const { user } = renderList("member");
    await screen.findByRole("link", { name: "Budget" });

    await user.selectOptions(screen.getByRole("combobox", { name: "Sort by" }), "title_desc");

    await waitFor(() => expect(navigation.url).toContain("sort=title_desc"));
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(
        WORKSPACE_ID,
        expect.objectContaining({ sort: "title_desc" }),
        expect.anything(),
      ),
    );
  });

  it("distinguishes 'nothing yet' from 'nothing matches'", async () => {
    vi.spyOn(documentApi, "list").mockResolvedValue(page([]));
    renderList("member", `${routes.documents(WORKSPACE_ID)}?q=zzz`);

    expect(await screen.findByText("No documents match")).toBeInTheDocument();
  });
});

describe("who can add documents", () => {
  it("invites a member to upload into an empty workspace", async () => {
    vi.spyOn(documentApi, "list").mockResolvedValue(page([]));
    renderList("member");

    expect(await screen.findByText("Add your first documents")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /upload/i }).length).toBeGreaterThan(0);
  });

  it("gives a viewer no way to upload -- no button, no dropzone, no file input", async () => {
    vi.spyOn(documentApi, "list").mockResolvedValue(page([]));
    renderList("viewer");

    expect(await screen.findByText("No documents yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /upload/i })).not.toBeInTheDocument();
    expect(screen.queryByText("Add your first documents")).not.toBeInTheDocument();
    expect(document.querySelector('input[type="file"]')).toBeNull();
    expect(screen.queryByRole("button", { name: "New folder" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Manage" })).not.toBeInTheDocument();
  });

  it("gives a viewer only Download in a row's menu", async () => {
    vi.spyOn(documentApi, "list").mockResolvedValue(page(["Budget"]));
    const { user } = renderList("viewer");

    await user.click(await screen.findByRole("button", { name: "Actions for Budget" }));

    const items = await screen.findAllByRole("menuitem");
    expect(items.map((item) => item.textContent)).toEqual(["Download"]);
  });

  it("gives a member the row's full menu, with delete last and marked as destructive", async () => {
    vi.spyOn(documentApi, "list").mockResolvedValue(page(["Budget"]));
    const { user } = renderList("member");

    await user.click(await screen.findByRole("button", { name: "Actions for Budget" }));

    const items = await screen.findAllByRole("menuitem");
    expect(items.map((item) => item.textContent?.trim())).toEqual(
      expect.arrayContaining(["Download", "Move to folder…", "Archive", "Delete…"]),
    );
    expect(items[items.length - 1]).toHaveTextContent("Delete…");
  });
});
