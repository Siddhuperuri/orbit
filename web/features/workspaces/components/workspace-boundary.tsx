"use client";

import Link from "next/link";
import { useEffect, type ReactNode } from "react";

import { ErrorState } from "@/components/feedback/error-state";
import { PageSkeleton } from "@/components/layout/page-skeleton";
import { Button } from "@/components/ui/button";
import { useWorkspaceQuery } from "@/features/workspaces/api/use-workspaces";
import { rememberWorkspace } from "@/features/workspaces/hooks/last-workspace";
import { WorkspaceProvider } from "@/features/workspaces/hooks/use-workspace-context";
import { ErrorCode, isApiError } from "@/lib/api/errors";
import { routes } from "@/lib/navigation";

/**
 * Resolves the workspace named in the URL before anything inside it renders, and
 * gives descendants its identity and the caller's role.
 *
 * "Not found" and "no access" look identical on purpose: the API answers 404 for a
 * workspace the caller is not a member of (ADR-0014), so that a guessed id reveals
 * nothing about whether it exists. The message here is equally neutral.
 */
export function WorkspaceBoundary({
  workspaceId,
  children,
}: {
  workspaceId: string;
  children: ReactNode;
}) {
  const query = useWorkspaceQuery(workspaceId);
  const workspace = query.data;

  useEffect(() => {
    if (workspace) rememberWorkspace(workspace.id);
  }, [workspace]);

  if (workspace) return <WorkspaceProvider workspace={workspace}>{children}</WorkspaceProvider>;

  if (query.isPending) return <PageSkeleton />;

  if (isApiError(query.error) && query.error.code === ErrorCode.NotFound) {
    return (
      <div className="mx-auto flex max-w-md flex-col items-center px-4 py-24 text-center">
        <h1 className="text-fg text-lg font-semibold">Workspace not found</h1>
        <p className="text-fg-muted mt-1.5 text-base">
          It may have been deleted, or you may not be a member of it.
        </p>
        <Button asChild variant="primary" className="mt-5">
          <Link href={routes.home}>Go to your workspaces</Link>
        </Button>
      </div>
    );
  }

  return (
    <ErrorState
      error={query.error}
      title="Couldn't open this workspace"
      onRetry={() => void query.refetch()}
      retrying={query.isFetching}
    />
  );
}
