"use client";

import { useParams } from "next/navigation";
import { useSyncExternalStore } from "react";

import { useWorkspaces } from "@/features/workspaces/api/use-workspaces";
import { readLastWorkspaceId } from "@/features/workspaces/hooks/last-workspace";
import type { Workspace } from "@/features/workspaces/types";

const subscribeNever = () => () => undefined;

/**
 * The workspace the shell should present as "current".
 *
 * Inside a workspace that is the one in the URL. On a page that belongs to no
 * workspace (Account, New workspace) it is the one the user was last in, so the
 * sidebar keeps its navigation instead of going blank the moment they check their
 * profile. `routeWorkspaceId` says which of the two it is.
 */
export function useActiveWorkspace(): {
  workspace: Workspace | undefined;
  routeWorkspaceId: string | undefined;
} {
  const { workspaceId: routeWorkspaceId } = useParams<{ workspaceId?: string }>();
  const { data: workspaces } = useWorkspaces();
  const lastId = useSyncExternalStore(subscribeNever, readLastWorkspaceId, () => null);

  const id = routeWorkspaceId ?? lastId;
  const workspace = workspaces?.find((candidate) => candidate.id === id);
  return { workspace, routeWorkspaceId };
}
