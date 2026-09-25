"use client";

import { CreateWorkspaceForm } from "@/features/workspaces/components/create-workspace-form";

/** First run: nothing else works until a workspace exists, so this is the whole screen. */
export function FirstWorkspacePanel() {
  return (
    <div className="mx-auto w-full max-w-md px-4 py-16 sm:py-24">
      <h1 className="text-fg text-xl font-semibold">Create your first workspace</h1>
      <p className="text-fg-muted mt-1.5 mb-6 text-base">
        A workspace holds a set of documents and the conversations about them. Everything you upload
        stays inside it, visible only to its members.
      </p>
      <CreateWorkspaceForm />
    </div>
  );
}
