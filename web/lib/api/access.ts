/**
 * GENERATED FILE -- DO NOT EDIT.
 *
 * The backend's role -> permission map (`orbit.domain.access.ROLE_PERMISSIONS`),
 * exported by `npm run api:types`. The UI uses it only to avoid *offering* actions
 * a role cannot perform; the API enforces every permission regardless.
 */

export const PERMISSIONS = [
  "audit:read",
  "chat:use",
  "document:create",
  "document:delete",
  "document:read",
  "document:update",
  "folder:read",
  "folder:write",
  "member:invite",
  "member:read",
  "member:remove",
  "member:update_role",
  "search:query",
  "tag:read",
  "tag:write",
  "workspace:delete",
  "workspace:read",
  "workspace:update",
] as const;

export type Permission = (typeof PERMISSIONS)[number];

export const ROLES = [
  "admin",
  "member",
  "owner",
  "viewer",
] as const;

export type RoleName = (typeof ROLES)[number];

export const ROLE_PERMISSIONS: Readonly<Record<RoleName, readonly Permission[]>> = {
  admin: [
    "audit:read",
    "chat:use",
    "document:create",
    "document:delete",
    "document:read",
    "document:update",
    "folder:read",
    "folder:write",
    "member:invite",
    "member:read",
    "member:remove",
    "member:update_role",
    "search:query",
    "tag:read",
    "tag:write",
    "workspace:read",
    "workspace:update",
  ],
  member: [
    "chat:use",
    "document:create",
    "document:delete",
    "document:read",
    "document:update",
    "folder:read",
    "folder:write",
    "member:read",
    "search:query",
    "tag:read",
    "tag:write",
    "workspace:read",
  ],
  owner: [
    "audit:read",
    "chat:use",
    "document:create",
    "document:delete",
    "document:read",
    "document:update",
    "folder:read",
    "folder:write",
    "member:invite",
    "member:read",
    "member:remove",
    "member:update_role",
    "search:query",
    "tag:read",
    "tag:write",
    "workspace:delete",
    "workspace:read",
    "workspace:update",
  ],
  viewer: [
    "chat:use",
    "document:read",
    "folder:read",
    "member:read",
    "search:query",
    "tag:read",
    "workspace:read",
  ],
};
