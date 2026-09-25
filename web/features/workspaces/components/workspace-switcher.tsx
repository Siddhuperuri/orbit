"use client";

import { Check, ChevronsUpDown, Plus } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useRef, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useWorkspaces } from "@/features/workspaces/api/use-workspaces";
import { CreateWorkspaceDialog } from "@/features/workspaces/components/create-workspace-dialog";
import { ROLE_LABELS } from "@/features/workspaces/permissions";
import { routes } from "@/lib/navigation";

/**
 * Shows the current workspace and switches between them. The trigger names the
 * workspace, not just "Workspaces", because knowing *where you are* matters in a
 * product that holds several people's private documents.
 */
export function WorkspaceSwitcher() {
  const { workspaceId } = useParams<{ workspaceId?: string }>();
  const { data: workspaces, isPending } = useWorkspaces();
  const [creating, setCreating] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);

  if (isPending) return <Skeleton className="h-9 w-full" />;

  const current = workspaces?.find((workspace) => workspace.id === workspaceId);

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            ref={triggerRef}
            type="button"
            className="border-line-strong bg-surface hover:bg-sunken flex h-9 w-full items-center justify-between gap-2 rounded-md border px-2.5 text-left text-base pointer-coarse:h-11"
          >
            <span className="min-w-0">
              <span className="sr-only">Workspace: </span>
              <span className="text-fg block truncate font-medium">
                {current?.name ?? "Choose a workspace"}
              </span>
            </span>
            <ChevronsUpDown className="text-fg-muted size-4 shrink-0" aria-hidden="true" />
          </button>
        </DropdownMenuTrigger>

        <DropdownMenuContent
          align="start"
          className="w-(--radix-dropdown-menu-trigger-width) min-w-56"
        >
          <DropdownMenuLabel>Your workspaces</DropdownMenuLabel>
          {workspaces?.map((workspace) => (
            <DropdownMenuItem key={workspace.id} asChild>
              <Link
                href={routes.documents(workspace.id)}
                aria-current={workspace.id === workspaceId ? "true" : undefined}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{workspace.name}</span>
                  {workspace.role ? (
                    <span className="text-fg-muted block text-xs">
                      {ROLE_LABELS[workspace.role]}
                    </span>
                  ) : null}
                </span>
                {workspace.id === workspaceId ? <Check aria-hidden="true" /> : null}
              </Link>
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setCreating(true)}>
            <Plus aria-hidden="true" />
            Create workspace
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <CreateWorkspaceDialog
        open={creating}
        onOpenChange={setCreating}
        returnFocusRef={triggerRef}
      />
    </>
  );
}
