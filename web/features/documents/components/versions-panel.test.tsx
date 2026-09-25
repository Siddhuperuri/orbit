import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { documentApi } from "@/features/documents/api/endpoints";
import { VersionsPanel } from "@/features/documents/components/versions-panel";
import { makeDocumentIn, makeVersion } from "@/test/factories";
import { WORKSPACE_ID, renderInWorkspace } from "@/test/render";

const DOC_ID = "doc-versions";

function stub() {
  const current = makeVersion({ id: "v2", version_number: 2, original_filename: "policy-v2.pdf" });
  const older = makeVersion({
    id: "v1",
    version_number: 1,
    is_current: false,
    original_filename: "policy-v1.pdf",
  });
  const document = makeDocumentIn("ready", { id: DOC_ID, current_version: current });
  vi.spyOn(documentApi, "versions").mockResolvedValue({
    items: [current, older],
    next_before: null,
  });
  return { document, current };
}

describe("a document's versions", () => {
  it("lists every version newest first, marking the current one, each downloadable", async () => {
    const { document } = stub();
    renderInWorkspace(<VersionsPanel document={document} />, { role: "viewer" });

    const rows = await screen.findAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Version 2");
    expect(rows[0]).toHaveTextContent("Current");
    expect(rows[1]).toHaveTextContent("Version 1");
    expect(rows[1]).not.toHaveTextContent("Current");
    expect(screen.getByRole("button", { name: "Download version 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download version 2" })).toBeInTheDocument();
  });

  it("says earlier versions keep their file but not their searchable text", async () => {
    const { document } = stub();
    renderInWorkspace(<VersionsPanel document={document} />, { role: "member" });

    expect(await screen.findByText(/no longer searchable or readable/)).toBeInTheDocument();
  });

  it("offers a viewer no upload", async () => {
    const { document } = stub();
    renderInWorkspace(<VersionsPanel document={document} />, { role: "viewer" });

    await screen.findAllByRole("listitem");
    expect(screen.queryByRole("button", { name: /Upload a new version/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId("version-input")).not.toBeInTheDocument();
  });

  it("uploads a chosen file as a new version of this document", async () => {
    const { document } = stub();
    const addVersion = vi
      .spyOn(documentApi, "addVersion")
      .mockResolvedValue({ document, deduplicated: false } as never);
    const { user } = renderInWorkspace(<VersionsPanel document={document} />, { role: "member" });
    await screen.findAllByRole("listitem");

    const file = new File(["# updated"], "policy-v3.md", { type: "text/markdown" });
    await user.upload(screen.getByTestId("version-input"), file);

    await waitFor(() => expect(addVersion).toHaveBeenCalledOnce());
    expect(addVersion).toHaveBeenCalledWith(
      WORKSPACE_ID,
      DOC_ID,
      expect.objectContaining({ file }),
    );
  });

  it("refuses an unsupported file before sending anything, and says why", async () => {
    const { document } = stub();
    const addVersion = vi.spyOn(documentApi, "addVersion");
    // The input's `accept` is a hint to the file picker, not a rule; the app checks as well.
    renderInWorkspace(<VersionsPanel document={document} />, { role: "member" });
    await screen.findAllByRole("listitem");

    // `fireEvent`, not `user.upload`: the latter honours `accept` and would never deliver the file.
    fireEvent.change(screen.getByTestId("version-input"), {
      target: { files: [new File(["MZ"], "setup.exe", { type: "application/octet-stream" })] },
    });

    expect(await screen.findByText(/Not uploaded/)).toBeInTheDocument();
    expect(addVersion).not.toHaveBeenCalled();
  });
});
