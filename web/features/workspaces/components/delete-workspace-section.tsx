"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { ConfirmDialog } from "@/components/feedback/confirm-dialog";
import { notify } from "@/components/feedback/notify";
import { Button } from "@/components/ui/button";
import { useDeleteWorkspace } from "@/features/workspaces/api/use-workspaces";
import { forgetLastWorkspace } from "@/features/workspaces/hooks/last-workspace";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { routes } from "@/lib/navigation";

/** Owner-only. Requires the workspace's name to be typed: this removes every document and conversation from view at once. */
export function DeleteWorkspaceSection() {
  const { workspace, can } = useWorkspace();
  const router = useRouter();
  const remove = useDeleteWorkspace(workspace.id);
  const [confirming, setConfirming] = useState(false);

  if (!can("workspace:delete")) {
    return (
      <p className="text-fg-muted text-base">Only the workspace&apos;s owner can delete it.</p>
    );
  }

  return (
    <>
      <p className="text-fg-muted mb-3 max-w-prose text-base">
        Deleting hides the workspace, its documents, and its conversations from every member
        immediately. The contents are retained rather than erased, so ask an operator if something
        was removed by mistake.
      </p>
      <Button variant="danger" onClick={() => setConfirming(true)}>
        Delete workspace…
      </Button>

      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={`Delete “${workspace.name}”?`}
        description="Every member loses access at once, and its documents and conversations disappear from ORBIT."
        confirmLabel="Delete workspace"
        pendingLabel="Deleting"
        typedConfirmation={workspace.name}
        onConfirm={async () => {
          await remove.mutateAsync();
          forgetLastWorkspace();
          notify.success(`Deleted “${workspace.name}”`);
          router.replace(routes.home);
        }}
      />
    </>
  );
}
