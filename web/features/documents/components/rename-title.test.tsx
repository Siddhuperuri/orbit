import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { documentApi } from "@/features/documents/api/endpoints";
import { DocumentDetail } from "@/features/documents/components/document-detail";
import { ApiError, ErrorCode } from "@/lib/api/errors";
import { routes } from "@/lib/navigation";
import { makeDocumentIn, makeVersion } from "@/test/factories";
import { WORKSPACE_ID, renderInWorkspace } from "@/test/render";

const DOC_ID = "doc-rename";

const conflict = () =>
  new ApiError({
    code: ErrorCode.Conflict,
    status: 409,
    message: "The document was modified by someone else. Reload and try again.",
    requestId: "req-409",
  });

function stubReads() {
  vi.spyOn(documentApi, "processing").mockResolvedValue({
    document_id: DOC_ID,
    version: makeVersion(),
    attempts: [],
  });
  vi.spyOn(documentApi, "versions").mockResolvedValue({ items: [], next_before: null });
  vi.spyOn(documentApi, "content").mockResolvedValue({
    items: [],
    next_after: null,
    total_passages: 0,
    version_id: "v",
    version_number: 1,
  });
}

async function openRenameForm(get: ReturnType<typeof vi.spyOn>) {
  stubReads();
  const utils = renderInWorkspace(<DocumentDetail documentId={DOC_ID} />, {
    role: "member",
    url: routes.document(WORKSPACE_ID, DOC_ID),
  });
  await screen.findByRole("heading", { level: 1, name: "Original title" });
  await utils.user.click(screen.getByRole("button", { name: "Rename Original title" }));
  const input = screen.getByRole("textbox", { name: "Document title" });
  return { ...utils, input, get };
}

describe("renaming a document", () => {
  it("sends the version the page read, so a concurrent edit is detected rather than overwritten", async () => {
    const original = makeDocumentIn("ready", { id: DOC_ID, title: "Original title", version: 3 });
    const get = vi.spyOn(documentApi, "get").mockResolvedValue(original);
    const update = vi
      .spyOn(documentApi, "update")
      .mockResolvedValue({ ...original, title: "Better title", version: 4 });
    const { user, input } = await openRenameForm(get);

    await user.clear(input);
    await user.type(input, "Better title");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(WORKSPACE_ID, DOC_ID, { title: "Better title" }, 3),
    );
    await waitFor(() =>
      expect(screen.queryByRole("textbox", { name: "Document title" })).not.toBeInTheDocument(),
    );
  });

  it("refuses an empty title without calling the server", async () => {
    const original = makeDocumentIn("ready", { id: DOC_ID, title: "Original title" });
    const get = vi.spyOn(documentApi, "get").mockResolvedValue(original);
    const update = vi.spyOn(documentApi, "update");
    const { user, input } = await openRenameForm(get);

    await user.clear(input);
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(
      await screen.findByText(/needs a title|is required|can't be empty/i),
    ).toBeInTheDocument();
    expect(update).not.toHaveBeenCalled();
  });

  it("on a conflict keeps what was typed, shows the other person's title, and makes saving again a deliberate overwrite", async () => {
    const original = makeDocumentIn("ready", { id: DOC_ID, title: "Original title", version: 1 });
    const theirs = { ...original, title: "A colleague's title", version: 2 };
    const get = vi.spyOn(documentApi, "get").mockResolvedValue(original);
    const update = vi
      .spyOn(documentApi, "update")
      .mockRejectedValueOnce(conflict())
      .mockResolvedValue({ ...theirs, title: "My title", version: 3 });
    const { user, input } = await openRenameForm(get);

    await user.clear(input);
    await user.type(input, "My title");
    get.mockResolvedValue(theirs); // what the refetch after the 409 will see
    await user.click(screen.getByRole("button", { name: "Save" }));

    // Said once, in plain words; the form stays open with the user's text intact.
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Someone else changed this document");
    expect(alert.textContent).not.toMatch(/Reload and try again/);
    expect(screen.getByRole("textbox", { name: "Document title" })).toHaveValue("My title");
    // The page behind it has caught up with the other person's version.
    expect(
      await screen.findByRole("heading", { level: 1, name: "A colleague's title" }),
    ).toBeInTheDocument();

    // Saving again is now an overwrite *of version 2* -- deliberate, and detected if it races again.
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(2));
    expect(update).toHaveBeenLastCalledWith(WORKSPACE_ID, DOC_ID, { title: "My title" }, 2);
  });
});

describe("who may rename", () => {
  it("does not offer a viewer a way to rename", async () => {
    stubReads();
    vi.spyOn(documentApi, "get").mockResolvedValue(
      makeDocumentIn("ready", { id: DOC_ID, title: "Original title" }),
    );
    renderInWorkspace(<DocumentDetail documentId={DOC_ID} />, { role: "viewer" });

    await screen.findByRole("heading", { level: 1, name: "Original title" });
    expect(screen.queryByRole("button", { name: /^Rename/ })).not.toBeInTheDocument();
  });
});
