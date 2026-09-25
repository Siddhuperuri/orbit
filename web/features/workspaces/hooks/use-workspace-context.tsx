"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";

import { roleCan } from "@/features/workspaces/permissions";
import type { Role, Workspace } from "@/features/workspaces/types";
import type { Permission } from "@/lib/api/access";

interface WorkspaceContextValue {
  workspace: Workspace;
  /** The caller's role here. Falls back to the least-privileged role if the API omitted it. */
  role: Role;
  /** Whether the caller's role permits an action. Presentation only; the API enforces. */
  can: (permission: Permission) => boolean;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({
  workspace,
  children,
}: {
  workspace: Workspace;
  children: ReactNode;
}) {
  const value = useMemo<WorkspaceContextValue>(() => {
    const role = workspace.role ?? "viewer";
    return { workspace, role, can: (permission) => roleCan(role, permission) };
  }, [workspace]);

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

/** The current workspace and the caller's role in it. Valid only below `WorkspaceBoundary`. */
export function useWorkspace(): WorkspaceContextValue {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("useWorkspace must be used inside <WorkspaceBoundary>.");
  return value;
}
