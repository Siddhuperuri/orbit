"use client";

import { useState } from "react";

import { ConfirmDialog } from "@/components/feedback/confirm-dialog";
import { notify } from "@/components/feedback/notify";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { CopyButton } from "@/components/feedback/copy-button";
import { useUser } from "@/features/auth/hooks/use-user";
import { useChangeMemberRole, useRemoveMember } from "@/features/workspaces/api/use-workspaces";
import { ROLE_LABELS } from "@/features/workspaces/permissions";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import type { Member, Role } from "@/features/workspaces/types";
import { formatDate, shortId } from "@/lib/utils/format";

const ROLES: Role[] = ["viewer", "member", "admin", "owner"];

function MemberRow({ member }: { member: Member }) {
  const { workspace, can, role: myRole } = useWorkspace();
  const user = useUser();
  const changeRole = useChangeMemberRole(workspace.id);
  const remove = useRemoveMember(workspace.id);
  const [confirming, setConfirming] = useState(false);

  const isSelf = member.user_id === user.id;
  const label = isSelf ? "you" : shortId(member.user_id);
  // Only an owner may hand out ownership; the API is the real gate, this just
  // avoids offering a choice that would be refused.
  const roleOptions = ROLES.filter((role) => role !== "owner" || myRole === "owner");

  return (
    <TableRow>
      <TableCell>
        <span className="flex items-center gap-1.5">
          <code className="text-fg font-mono text-sm">{shortId(member.user_id)}</code>
          <CopyButton value={member.user_id} label={`Copy account ID of member ${label}`} />
          {isSelf ? <Badge tone="accent">You</Badge> : null}
        </span>
      </TableCell>
      <TableCell>
        {can("member:update_role") ? (
          <NativeSelect
            aria-label={`Role of member ${label}`}
            className="w-32"
            value={member.role}
            disabled={changeRole.isPending}
            onChange={(event) =>
              changeRole.mutate({ userId: member.user_id, role: event.target.value as Role })
            }
          >
            {roleOptions.map((role) => (
              <option key={role} value={role}>
                {ROLE_LABELS[role]}
              </option>
            ))}
          </NativeSelect>
        ) : (
          ROLE_LABELS[member.role]
        )}
      </TableCell>
      <TableCell className="text-fg-muted hidden sm:table-cell">
        {formatDate(member.created_at)}
      </TableCell>
      <TableCell className="text-right">
        {can("member:remove") ? (
          <>
            <Button
              size="sm"
              variant="ghost"
              className="text-danger"
              aria-label={`Remove member ${label}`}
              onClick={() => setConfirming(true)}
            >
              {isSelf ? "Leave" : "Remove"}
            </Button>
            <ConfirmDialog
              open={confirming}
              onOpenChange={setConfirming}
              title={isSelf ? "Leave this workspace?" : "Remove this member?"}
              description={
                isSelf
                  ? "You'll lose access to its documents and conversations immediately."
                  : `Member ${shortId(member.user_id)} loses access to this workspace immediately.`
              }
              confirmLabel={isSelf ? "Leave workspace" : "Remove member"}
              pendingLabel="Removing"
              onConfirm={async () => {
                await remove.mutateAsync(member.user_id);
                notify.success(isSelf ? "You left the workspace" : "Member removed");
              }}
            />
          </>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

export function MembersTable({ members }: { members: readonly Member[] }) {
  return (
    <Table>
      <TableCaption>Members of this workspace</TableCaption>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead>Account</TableHead>
          <TableHead>Role</TableHead>
          <TableHead className="hidden sm:table-cell">Added</TableHead>
          <TableHead className="w-24">
            <span className="sr-only">Actions</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {members.map((member) => (
          <MemberRow key={member.user_id} member={member} />
        ))}
      </TableBody>
    </Table>
  );
}
