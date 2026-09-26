"use client";

import { PageContainer } from "@/components/layout/page-container";
import { PageHeader } from "@/components/layout/page-header";
import { SettingsSection } from "@/components/layout/settings-section";
import { DeleteWorkspaceSection } from "@/features/workspaces/components/delete-workspace-section";
import { RenameWorkspaceForm } from "@/features/workspaces/components/rename-workspace-form";
import { ROLE_LABELS } from "@/features/workspaces/permissions";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { formatDate } from "@/lib/utils/format";

export function WorkspaceSettingsPage() {
  const { workspace, role } = useWorkspace();

  return (
    <PageContainer>
      <PageHeader
        eyebrow={workspace.name}
        title="Settings"
        description="The workspace's name, its identifiers, and deleting it."
      />

      <SettingsSection title="Name" description="How the workspace appears to every member.">
        <RenameWorkspaceForm />
      </SettingsSection>

      <SettingsSection title="Details" description="Identifiers support may ask you for.">
        <dl className="divide-line -my-2.5 divide-y text-base">
          <div className="grid gap-x-6 gap-y-0.5 py-2.5 sm:grid-cols-[9rem_1fr]">
            <dt className="text-fg-muted text-sm font-medium">Your role</dt>
            <dd>{ROLE_LABELS[role]}</dd>
          </div>
          <div className="grid gap-x-6 gap-y-0.5 py-2.5 sm:grid-cols-[9rem_1fr]">
            <dt className="text-fg-muted text-sm font-medium">Slug</dt>
            <dd className="font-mono text-sm">{workspace.slug}</dd>
          </div>
          <div className="grid gap-x-6 gap-y-0.5 py-2.5 sm:grid-cols-[9rem_1fr]">
            <dt className="text-fg-muted text-sm font-medium">Created</dt>
            <dd>{formatDate(workspace.created_at)}</dd>
          </div>
          <div className="grid gap-x-6 gap-y-0.5 py-2.5 sm:grid-cols-[9rem_1fr]">
            <dt className="text-fg-muted text-sm font-medium">Workspace ID</dt>
            <dd className="font-mono text-xs break-all">{workspace.id}</dd>
          </div>
        </dl>
      </SettingsSection>

      <SettingsSection
        title="Delete workspace"
        description="Removes it for every member."
        tone="danger"
      >
        <DeleteWorkspaceSection />
      </SettingsSection>
    </PageContainer>
  );
}
