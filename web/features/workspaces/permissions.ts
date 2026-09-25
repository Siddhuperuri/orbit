import { ROLE_PERMISSIONS, type Permission } from "@/lib/api/access";
import type { Role } from "@/features/workspaces/types";

/**
 * Whether a role may perform an action -- used to avoid *offering* a button that
 * would only produce a 403.
 *
 * This is presentation, not protection: the map is generated from the backend's
 * own `ROLE_PERMISSIONS` (so it cannot drift), and the API re-checks every
 * permission on every request. Hiding a control is a courtesy; the server's
 * refusal is the control.
 *
 * An unknown or missing role gets nothing, so a future role the generated map does
 * not yet know about fails closed.
 */
export function roleCan(role: Role | null | undefined, permission: Permission): boolean {
  if (!role) return false;
  return ROLE_PERMISSIONS[role]?.includes(permission) ?? false;
}

/** How a role reads in the interface. */
export const ROLE_LABELS: Record<Role, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
  viewer: "Viewer",
};

export const ROLE_DESCRIPTIONS: Record<Role, string> = {
  owner: "Full control, including deleting the workspace.",
  admin: "Manage members and workspace settings.",
  member: "Add, edit, and delete documents.",
  viewer: "Read documents, search, and ask questions.",
};
