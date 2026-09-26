"use client";

import { Check, ChevronsUpDown, Plus } from "lucide-react";
import Link from "next/link";
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
import { WorkspaceAvatar } from "@/features/workspaces/components/workspace-avatar";
import { useActiveWorkspace } from "@/features/workspaces/hooks/use-active-workspace";
import { ROLE_LABELS } from "@/features/workspaces/permissions";
import { routes } from "@/lib/navigation";

/**
 * Shows the current workspace and switches between them. The trigger names the
 * workspace, not just "Workspaces", because knowing *where you are* matters in a
 * product that holds several people's private documents.
 */
export function WorkspaceSwitcher() {
  const { workspace: current } = useActiveWorkspace();
  const { data: workspaces, isPending } = useWorkspaces();
  const [creating, setCreating] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);

  if (isPending) return <Skeleton className="h-11 w-full" />;

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            ref={triggerRef}
            type="button"
            className="hover:bg-fill data-[state=open]:bg-fill group flex h-12 w-full items-center gap-3 px-3 text-left text-base transition-colors duration-300"
          >
            <WorkspaceAvatar name={current?.name} className="size-8" />
            <span className="min-w-0 flex-1">
              <span className="sr-only">Workspace: </span>
              <span className="text-fg block truncate leading-tight font-semibold">
                {current?.name ?? "Choose a workspace"}
              </span>
              {current?.role ? (
                <span className="label-micro text-fg-subtle block truncate">
                  {ROLE_LABELS[current.role]}
                </span>
              ) : null}
            </span>
            <ChevronsUpDown
              className="text-fg-subtle group-hover:text-fg-muted size-4 shrink-0"
              aria-hidden="true"
            />
          </button>
        </DropdownMenuTrigger>

        <DropdownMenuContent
          align="start"
          className="w-(--radix-dropdown-menu-trigger-width) min-w-60"
        >
          <DropdownMenuLabel>Your workspaces</DropdownMenuLabel>
          {workspaces?.map((workspace) => (
            <DropdownMenuItem key={workspace.id} asChild>
              <Link
                href={routes.documents(workspace.id)}
                aria-current={workspace.id === current?.id ? "true" : undefined}
              >
                <WorkspaceAvatar name={workspace.name} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{workspace.name}</span>
                  {workspace.role ? (
                    <span className="text-fg-muted block text-xs">
                      {ROLE_LABELS[workspace.role]}
                    </span>
                  ) : null}
                </span>
                {workspace.id === current?.id ? <Check aria-hidden="true" /> : null}
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
