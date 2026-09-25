import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { folderApi } from "@/features/folders/api/endpoints";
import { DeleteFolderDialog } from "@/features/folders/components/delete-folder-dialog";
import { makeFolder } from "@/test/factories";
import { WORKSPACE_ID, renderInWorkspace } from "@/test/render";

const open = (folder: ReturnType<typeof makeFolder>) =>
  renderInWorkspace(<DeleteFolderDialog open folder={folder} onOpenChange={() => undefined} />, {
    role: "member",
  });

describe("deleting a folder", () => {
  it("refuses, and offers nothing destructive, when the folder still holds documents", () => {
    open(makeFolder({ id: "f1", name: "Policies", document_count: 2, archived_document_count: 1 }));

    expect(screen.getByRole("heading", { name: "“Policies” isn't empty" })).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toHaveTextContent("3 documents (1 archived)");
    expect(screen.queryByRole("button", { name: /^Delete/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Show its documents" })).toHaveAttribute(
      "href",
      expect.stringContaining("f1"),
    );
  });

  it("says ORBIT will not move or remove anything on the user's behalf", () => {
    open(makeFolder({ name: "Policies", document_count: 1, archived_document_count: 0 }));

    expect(screen.getByRole("dialog")).toHaveTextContent("won't move or remove anything");
  });

  it("points at archived documents when those are all that keep the folder from being deleted", () => {
    open(makeFolder({ id: "f2", name: "Old", document_count: 0, archived_document_count: 3 }));

    expect(screen.getByRole("link", { name: "Show its documents" })).toHaveAttribute(
      "href",
      expect.stringContaining("archived"),
    );
  });

  it("confirms an empty folder's deletion by name, and deletes it only when asked", async () => {
    const remove = vi.spyOn(folderApi, "remove").mockResolvedValue(undefined as never);
    const { user } = open(
      makeFolder({ id: "f3", name: "Scratch", document_count: 0, archived_document_count: 0 }),
    );

    expect(screen.getByRole("heading", { name: "Delete folder “Scratch”?" })).toBeInTheDocument();
    expect(remove).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Delete folder" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith(WORKSPACE_ID, "f3"));
  });
});
