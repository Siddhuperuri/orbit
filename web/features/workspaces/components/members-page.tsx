"use client";

import { QueryBoundary } from "@/components/feedback/query-boundary";
import { PageContainer } from "@/components/layout/page-container";
import { PageHeader } from "@/components/layout/page-header";
import { SettingsSection } from "@/components/layout/settings-section";
import { LoadingRegion, Skeleton } from "@/components/ui/skeleton";
import { useMembers } from "@/features/workspaces/api/use-workspaces";
import { InviteMemberForm } from "@/features/workspaces/components/invite-member-form";
import { MembersTable } from "@/features/workspaces/components/members-table";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { pluralize } from "@/lib/utils/format";

export function MembersPage() {
  const { workspace, can } = useWorkspace();
  const members = useMembers(workspace.id);

  return (
    <PageContainer>
      <PageHeader
        eyebrow={workspace.name}
        title="Members"
        description="Everyone who can open this workspace, and what each of them may do."
      />

      <SettingsSection
        title="People"
        description={
          members.data
            ? `${pluralize(members.data.length, "person", "people")} can access this workspace.`
            : undefined
        }
      >
        <QueryBoundary
          query={members}
          errorTitle="Couldn't load members"
          loading={
            <LoadingRegion label="Loading members" className="space-y-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </LoadingRegion>
          }
        >
          {(list) => <MembersTable members={list} />}
        </QueryBoundary>
      </SettingsSection>

      {can("member:invite") ? (
        <SettingsSection
          title="Add a member"
          description="Add an existing ORBIT account to this workspace."
        >
          <InviteMemberForm workspaceId={workspace.id} />
        </SettingsSection>
      ) : null}
    </PageContainer>
  );
}
