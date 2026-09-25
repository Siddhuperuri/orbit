"use client";

import { PageContainer } from "@/components/layout/page-container";
import { PageHeader } from "@/components/layout/page-header";
import { SettingsSection } from "@/components/layout/settings-section";
import { DeleteWorkspaceSection } from "@/features/workspaces/components/delete-workspace-section";
import { RenameWorkspaceForm } from "@/features/workspaces/components/rename-workspace-form";
import { WorkspaceSettingsNav } from "@/features/workspaces/components/workspace-settings-nav";
import { ROLE_LABELS } from "@/features/workspaces/permissions";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { formatDate } from "@/lib/utils/format";

export function WorkspaceSettingsPage() {
  const { workspace, role } = useWorkspace();

  return (
    <PageContainer width="narrow">
      <PageHeader title="Workspace settings" description={workspace.name} />
      <WorkspaceSettingsNav />

      <SettingsSection title="Name">
        <RenameWorkspaceForm />
      </SettingsSection>

      <SettingsSection title="Details">
        <dl className="grid max-w-md grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-base">
          <dt className="text-fg-muted">Your role</dt>
          <dd>{ROLE_LABELS[role]}</dd>
          <dt className="text-fg-muted">Slug</dt>
          <dd className="font-mono text-sm">{workspace.slug}</dd>
          <dt className="text-fg-muted">Created</dt>
          <dd>{formatDate(workspace.created_at)}</dd>
          <dt className="text-fg-muted">Workspace ID</dt>
          <dd className="font-mono text-xs break-all">{workspace.id}</dd>
        </dl>
      </SettingsSection>

      <SettingsSection title="Delete workspace">
        <DeleteWorkspaceSection />
      </SettingsSection>
    </PageContainer>
  );
}
