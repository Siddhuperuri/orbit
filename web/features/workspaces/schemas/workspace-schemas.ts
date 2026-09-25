import { z } from "zod";

export const MAX_WORKSPACE_NAME_LENGTH = 200;

const name = z
  .string()
  .trim()
  .min(1, "Give the workspace a name.")
  .max(MAX_WORKSPACE_NAME_LENGTH, `Use ${MAX_WORKSPACE_NAME_LENGTH} characters or fewer.`);

export const workspaceNameSchema = z.object({ name });
export type WorkspaceNameValues = z.infer<typeof workspaceNameSchema>;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const inviteMemberSchema = z.object({
  user_id: z.string().trim().regex(UUID, "Enter the person's account ID (a UUID)."),
  role: z.enum(["admin", "member", "viewer"]),
});
export type InviteMemberValues = z.infer<typeof inviteMemberSchema>;
