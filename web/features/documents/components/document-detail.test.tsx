import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { documentApi } from "@/features/documents/api/endpoints";
import { documentKeys } from "@/features/documents/api/keys";
import { DocumentDetail } from "@/features/documents/components/document-detail";
import type { Document } from "@/features/documents/types";
import { ApiError, ErrorCode } from "@/lib/api/errors";
import { routes } from "@/lib/navigation";
import { makeDocument, makeDocumentIn, makeTagRef, makeVersion } from "@/test/factories";
import { navigation } from "@/test/navigation";
import { WORKSPACE_ID, renderInWorkspace } from "@/test/render";

vi.mock("@/lib/utils/download", () => ({ startDownload: vi.fn() }));

const DOC_ID = "doc-detail";

const notFound = () =>
  new ApiError({
    code: ErrorCode.NotFound,
    status: 404,
    message: "Document not found.",
    requestId: "req-404",
  });

/** The calls a detail page makes, answered from `document`; returns the ones tests assert on. */
function stubDocument(document: Document) {
  const version = document.current_version;
  const get = vi.spyOn(documentApi, "get").mockResolvedValue(document);
  vi.spyOn(documentApi, "processing").mockResolvedValue({
    document_id: document.id,
    version: version ?? makeVersion(),
    attempts: [],
  });
  vi.spyOn(documentApi, "versions").mockResolvedValue({
    items: version ? [version] : [],
    next_before: null,
  });
  vi.spyOn(documentApi, "content").mockResolvedValue({
    items: [
      {
        ordinal: 0,
        text: "The travel policy says to book early.",
        heading_path: null,
        page_from: 1,
        page_to: 1,
        char_start: 0,
        char_end: 37,
      },
    ],
    next_after: null,
    total_passages: 1,
    version_id: version?.id ?? "v",
    version_number: version?.version_number ?? 1,
  });
  return { get };
}

const readyDocument = (overrides: Partial<Document> = {}) =>
  makeDocumentIn("ready", { id: DOC_ID, title: "Travel Policy", ...overrides });

async function renderDetail(role: "owner" | "admin" | "member" | "viewer", document: Document) {
  const spies = stubDocument(document);
  const utils = renderInWorkspace(<DocumentDetail documentId={document.id} />, {
    role,
    url: routes.document(WORKSPACE_ID, document.id),
  });
  await screen.findByRole("heading", { level: 1, name: document.title });
  return { ...spies, ...utils };
}

describe("a document's page, by role", () => {
  beforeEach(() => navigation.reset());

  it("offers a member everything they may do with the document", async () => {
    await renderDetail("member", readyDocument({ tags: [makeTagRef({ name: "Finance" })] }));

    expect(screen.getByRole("button", { name: "Rename Travel Policy" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "More actions" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Move…" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit tags" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove tag Finance" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload a new version…" })).toBeInTheDocument();
  });

  it("shows a viewer the document and how to download it -- and nothing they could not do", async () => {
    await renderDetail("viewer", readyDocument({ tags: [makeTagRef({ name: "Finance" })] }));

    // What reading allows.
    expect(screen.getByRole("button", { name: "Download" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download original" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Download version 1" })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Tags" })).toHaveTextContent("Finance");

    // Not disabled -- absent. A control that explains why it doesn't work is still a control.
    for (const name of [
      /^Rename/,
      "More actions",
      "Move…",
      "Edit tags",
      "Remove tag Finance",
      "Upload a new version…",
    ]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
    expect(document.querySelector('input[type="file"]')).toBeNull();
  });

  it("does not offer a viewer a retry on a failed document, but still says why it failed", async () => {
    await renderDetail("viewer", makeDocumentIn("failed", { id: DOC_ID, title: "Broken scan" }));

    expect(screen.getByText("The file appears to be damaged.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("lets a member restart a failed document", async () => {
    const failed = makeDocumentIn("failed", { id: DOC_ID, title: "Broken scan" });
    const reprocess = vi
      .spyOn(documentApi, "reprocess")
      .mockResolvedValue(makeDocumentIn("pending", { id: DOC_ID, title: "Broken scan" }));
    const { user } = await renderDetail("member", failed);

    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(reprocess).toHaveBeenCalledWith(WORKSPACE_ID, DOC_ID);
  });

  it("says an archived document is archived, and lets only an editor restore it", async () => {
    const archived = readyDocument({ archived_at: "2026-09-19T12:00:00Z" });

    const member = await renderDetail("member", archived);
    expect(screen.getByText("This document is archived.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restore" })).toBeInTheDocument();
    member.unmount();

    await renderDetail("viewer", archived);
    expect(screen.getByText("This document is archived.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restore" })).not.toBeInTheDocument();
  });
});

describe("deleting a document", () => {
  beforeEach(() => navigation.reset());

  async function openDeleteDialog() {
    const utils = await renderDetail("member", readyDocument());
    await utils.user.click(screen.getByRole("button", { name: "More actions" }));
    await utils.user.click(await screen.findByRole("menuitem", { name: "Delete…" }));
    return { ...utils, dialog: await screen.findByRole("dialog") };
  }

  it("asks first, by name, and says what it costs and what the alternative is", async () => {
    const { dialog } = await openDeleteDialog();

    expect(within(dialog).getByRole("heading", { name: "Delete “Travel Policy”?" })).toBeVisible();
    expect(dialog).toHaveTextContent("This can't be undone");
    expect(dialog).toHaveTextContent("archive it instead");
  });

  it("deletes nothing when cancelled", async () => {
    const remove = vi.spyOn(documentApi, "remove").mockResolvedValue(undefined as never);
    const { user, dialog } = await openDeleteDialog();

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(remove).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(navigation.url).toBe(routes.document(WORKSPACE_ID, DOC_ID));
  });

  it("deletes on confirmation and returns to the list", async () => {
    const remove = vi.spyOn(documentApi, "remove").mockResolvedValue(undefined as never);
    const { user, dialog } = await openDeleteDialog();

    await user.click(within(dialog).getByRole("button", { name: "Delete document" }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith(WORKSPACE_ID, DOC_ID));
    await waitFor(() => expect(navigation.url).toBe(routes.documents(WORKSPACE_ID)));
  });

  it("treats a document someone else already deleted as deleted, not as an error", async () => {
    vi.spyOn(documentApi, "remove").mockRejectedValue(notFound());
    const { user, dialog } = await openDeleteDialog();

    await user.click(within(dialog).getByRole("button", { name: "Delete document" }));

    await waitFor(() => expect(navigation.url).toBe(routes.documents(WORKSPACE_ID)));
  });

  it("keeps the dialog open and says why when the server refuses", async () => {
    vi.spyOn(documentApi, "remove").mockRejectedValue(
      new ApiError({
        code: ErrorCode.PermissionDenied,
        status: 403,
        message: "You do not have permission to perform this action.",
        requestId: "req-403",
      }),
    );
    const { user, dialog } = await openDeleteDialog();

    await user.click(within(dialog).getByRole("button", { name: "Delete document" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("permission");
    expect(navigation.url).toBe(routes.document(WORKSPACE_ID, DOC_ID));
  });
});

describe("a document that changes while it is open", () => {
  beforeEach(() => navigation.reset());

  it("says so when it turns out to be gone, keeps the last copy readable, and stops offering to change it", async () => {
    const { get, client } = await renderDetail("member", readyDocument());
    await screen.findByText("The travel policy says to book early.");

    // Someone else deletes it; this page finds out on its next refresh.
    get.mockRejectedValue(notFound());
    vi.spyOn(documentApi, "versions").mockRejectedValue(notFound());
    vi.spyOn(documentApi, "content").mockRejectedValue(notFound());
    await client.refetchQueries({ queryKey: documentKeys.detail(WORKSPACE_ID, DOC_ID) });

    const alert = await screen.findByText("This document is no longer available.");
    expect(alert.closest('[role="alert"]')).toHaveTextContent("last copy this page loaded");
    expect(screen.getByRole("link", { name: "Back to documents" })).toHaveAttribute(
      "href",
      routes.documents(WORKSPACE_ID),
    );

    // What was on screen stays; what could only 404 is gone. Reads, writes, and downloads.
    expect(screen.getByRole("heading", { level: 1, name: "Travel Policy" })).toBeInTheDocument();
    expect(screen.getByText("The travel policy says to book early.")).toBeInTheDocument();
    for (const name of [
      /^Rename/,
      "Download",
      "Download original",
      "More actions",
      "Move…",
      "Edit tags",
      "Upload a new version…",
      /^Download version/,
    ]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
    expect(
      screen.queryByRole("region", { name: "Ask about this document" }),
    ).not.toBeInTheDocument();

    // The failed refreshes of the panels around it are the same news, not more of it.
    expect(screen.queryByText(/Couldn't refresh/)).not.toBeInTheDocument();
  });

  it("says the document doesn't exist when it was never there to begin with", async () => {
    vi.spyOn(documentApi, "get").mockRejectedValue(notFound());
    renderInWorkspace(<DocumentDetail documentId="missing" />, { role: "member" });

    expect(await screen.findByRole("heading", { name: "Document not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to documents" })).toBeInTheDocument();
  });

  it("follows a document that finishes processing while it is open", async () => {
    const processing = makeDocumentIn(
      "processing",
      { id: DOC_ID, title: "Travel Policy" },
      "parse",
    );
    const { get, client } = await renderDetail("member", processing);
    expect(screen.getByText(/Being read and split into passages/)).toBeInTheDocument();

    get.mockResolvedValue(readyDocument());
    await client.refetchQueries({ queryKey: documentKeys.detail(WORKSPACE_ID, DOC_ID) });

    expect(await screen.findByText(/Indexed\. It can be searched/)).toBeInTheDocument();
  });

  it("never invents a percentage", async () => {
    await renderDetail(
      "member",
      makeDocumentIn("processing", { id: DOC_ID, title: "Travel Policy" }, "embed"),
    );

    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\d+\s?%/);
  });
});

describe("downloading", () => {
  beforeEach(() => navigation.reset());

  it("asks the API for a fresh link at click time and starts the download from it", async () => {
    const { startDownload } = await import("@/lib/utils/download");
    const link = vi.spyOn(documentApi, "downloadLink").mockResolvedValue({
      url: "https://storage.example/file?sig=abc",
      expires_in_seconds: 60,
    } as never);
    const { user } = await renderDetail("viewer", readyDocument());

    await user.click(screen.getByRole("button", { name: "Download" }));

    await waitFor(() => expect(link).toHaveBeenCalledWith(WORKSPACE_ID, DOC_ID));
    await waitFor(() =>
      expect(startDownload).toHaveBeenCalledWith("https://storage.example/file?sig=abc"),
    );
  });
});

// A document with no file yet (upload row exists, version not yet) must not crash the page.
describe("a document without a version", () => {
  it("renders, and does not offer to download a file that does not exist", async () => {
    await renderDetail(
      "member",
      makeDocument({ id: DOC_ID, title: "Empty", current_version: null }),
    );

    expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();
    expect(screen.getByText("There is no file for this document yet.")).toBeInTheDocument();
  });
});
