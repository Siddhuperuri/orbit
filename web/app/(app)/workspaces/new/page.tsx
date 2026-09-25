import type { Metadata } from "next";

import { FirstWorkspacePanel } from "@/features/workspaces/components/first-workspace-panel";

export const metadata: Metadata = { title: "New workspace" };

export default function NewWorkspacePage() {
  return <FirstWorkspacePanel />;
}
