"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { ErrorState } from "@/components/feedback/error-state";
import { PageSkeleton } from "@/components/layout/page-skeleton";
import { useWorkspaces } from "@/features/workspaces/api/use-workspaces";
import {
  forgetLastWorkspace,
  readLastWorkspaceId,
} from "@/features/workspaces/hooks/last-workspace";
import { routes } from "@/lib/navigation";

/**
 * Decides where `/` goes: the workspace last used in this browser if the user can
 * still reach it, else their first one, else the first-run screen.
 */
export function RootRedirect() {
  const router = useRouter();
  const { data: workspaces, error, refetch, isFetching } = useWorkspaces();

  useEffect(() => {
    if (!workspaces) return;

    if (workspaces.length === 0) {
      router.replace(routes.newWorkspace);
      return;
    }

    const lastId = readLastWorkspaceId();
    const target = workspaces.find((workspace) => workspace.id === lastId) ?? workspaces[0];
    if (lastId && target?.id !== lastId) forgetLastWorkspace();
    if (target) router.replace(routes.documents(target.id));
  }, [workspaces, router]);

  if (error && !workspaces) {
    return (
      <ErrorState
        error={error}
        title="Couldn't load your workspaces"
        onRetry={() => void refetch()}
        retrying={isFetching}
      />
    );
  }

  return <PageSkeleton />;
}
