"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { workspaceApi } from "@/features/workspaces/api/endpoints";
import { workspaceKeys } from "@/features/workspaces/api/keys";
import type { Role, Workspace } from "@/features/workspaces/types";

export function useWorkspaces() {
  return useQuery({
    queryKey: workspaceKeys.list(),
    queryFn: ({ signal }) => workspaceApi.list(signal),
  });
}

export function useWorkspaceQuery(workspaceId: string) {
  return useQuery({
    queryKey: workspaceKeys.detail(workspaceId),
    queryFn: ({ signal }) => workspaceApi.get(workspaceId, signal),
  });
}

export function useCreateWorkspace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => workspaceApi.create(name),
    meta: { handledLocally: true },
    onSuccess: (workspace) => {
      // The create response has no role (it is always the caller's own, as owner);
      // seed the detail cache so the new workspace opens without a loading state.
      queryClient.setQueryData<Workspace>(workspaceKeys.detail(workspace.id), {
        ...workspace,
        role: "owner",
      });
      return queryClient.invalidateQueries({ queryKey: workspaceKeys.list() });
    },
  });
}

export function useRenameWorkspace(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, expectedVersion }: { name: string; expectedVersion: number }) =>
      workspaceApi.rename(workspaceId, name, expectedVersion),
    meta: { handledLocally: true },
    onSuccess: (workspace) => {
      const role = queryClient.getQueryData<Workspace>(workspaceKeys.detail(workspaceId))?.role;
      queryClient.setQueryData<Workspace>(workspaceKeys.detail(workspaceId), {
        ...workspace,
        role: workspace.role ?? role,
      });
      return queryClient.invalidateQueries({ queryKey: workspaceKeys.list() });
    },
    onError: () =>
      // A 409 means someone else changed it; the next read must be fresh.
      queryClient.invalidateQueries({ queryKey: workspaceKeys.detail(workspaceId) }),
  });
}

export function useDeleteWorkspace(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => workspaceApi.remove(workspaceId),
    meta: { handledLocally: true },
    onSuccess: async () => {
      // Drop everything cached about the workspace: it is gone from the user's
      // point of view, and a stale copy must not be reachable by navigating back.
      queryClient.removeQueries({ queryKey: workspaceKeys.scope(workspaceId) });
      await queryClient.invalidateQueries({ queryKey: workspaceKeys.list() });
    },
  });
}

export function useMembers(workspaceId: string) {
  return useQuery({
    queryKey: workspaceKeys.members(workspaceId),
    queryFn: ({ signal }) => workspaceApi.members(workspaceId, signal),
  });
}

export function useInviteMember(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: Role }) =>
      workspaceApi.invite(workspaceId, userId, role),
    meta: { handledLocally: true },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: workspaceKeys.members(workspaceId) }),
  });
}

export function useChangeMemberRole(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: Role }) =>
      workspaceApi.changeRole(workspaceId, userId, role),
    meta: { errorTitle: "Couldn't change the role" },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: workspaceKeys.members(workspaceId) }),
  });
}

export function useRemoveMember(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => workspaceApi.removeMember(workspaceId, userId),
    meta: { handledLocally: true },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: workspaceKeys.members(workspaceId) }),
  });
}
