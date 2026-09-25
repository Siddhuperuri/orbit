import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog } from "@/components/feedback/confirm-dialog";
import { ApiError } from "@/lib/api/errors";

function renderDialog(overrides: Partial<React.ComponentProps<typeof ConfirmDialog>> = {}) {
  const onOpenChange = vi.fn();
  const onConfirm = vi.fn().mockResolvedValue(undefined);
  render(
    <ConfirmDialog
      open
      onOpenChange={onOpenChange}
      title="Delete “Budget”?"
      description="This can't be undone."
      confirmLabel="Delete document"
      onConfirm={onConfirm}
      {...overrides}
    />,
  );
  return { onOpenChange, onConfirm };
}

describe("ConfirmDialog", () => {
  it("is a named, described dialog that names the resource being destroyed", () => {
    renderDialog();
    const dialog = screen.getByRole("dialog", { name: "Delete “Budget”?" });
    expect(dialog).toHaveAccessibleDescription("This can't be undone.");
  });

  it("confirms, then closes", async () => {
    const { onConfirm, onOpenChange } = renderDialog();
    await userEvent.click(screen.getByRole("button", { name: "Delete document" }));

    expect(onConfirm).toHaveBeenCalledOnce();
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it("does nothing when cancelled", async () => {
    const { onConfirm, onOpenChange } = renderDialog();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onConfirm).not.toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("stays open and says why when the action fails, so the failure is not lost", async () => {
    const failure = new ApiError({
      code: "PERMISSION_DENIED",
      status: 403,
      message: "You do not have permission to perform this action.",
      requestId: "req-403",
    });
    const { onOpenChange } = renderDialog({ onConfirm: vi.fn().mockRejectedValue(failure) });

    await userEvent.click(screen.getByRole("button", { name: "Delete document" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("permission");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  describe("with typed confirmation", () => {
    it("keeps the destructive button disabled until the exact name is typed", async () => {
      const { onConfirm } = renderDialog({ typedConfirmation: "Research Library" });
      const confirm = screen.getByRole("button", { name: "Delete document" });
      const field = screen.getByLabelText(/Type .* to confirm/);

      expect(confirm).toBeDisabled();

      await userEvent.type(field, "research library"); // wrong case
      expect(confirm).toBeDisabled();

      await userEvent.clear(field);
      await userEvent.type(field, "Research Library");
      expect(confirm).toBeEnabled();

      await userEvent.click(confirm);
      expect(onConfirm).toHaveBeenCalledOnce();
    });
  });
});
