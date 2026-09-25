import type { Metadata } from "next";

import { WorkspaceSettingsPage } from "@/features/workspaces/components/workspace-settings-page";

export const metadata: Metadata = { title: "Workspace settings" };

export default function SettingsPage() {
  return <WorkspaceSettingsPage />;
}
