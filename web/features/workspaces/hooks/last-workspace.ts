import { readStorage, StorageKey, writeStorage } from "@/lib/storage";

/**
 * The workspace to open when the user arrives at `/`. A per-browser convenience
 * only: if it names a workspace the user can no longer reach, the caller falls
 * back to their first one.
 */
export function readLastWorkspaceId(): string | null {
  return readStorage(StorageKey.lastWorkspace);
}

export function rememberWorkspace(workspaceId: string): void {
  writeStorage(StorageKey.lastWorkspace, workspaceId);
}

export function forgetLastWorkspace(): void {
  writeStorage(StorageKey.lastWorkspace, null);
}
