import { describe, expect, it } from "vitest";

import { roleCan } from "@/features/workspaces/permissions";

describe("roleCan", () => {
  it("lets a viewer read and ask but not change anything", () => {
    expect(roleCan("viewer", "document:read")).toBe(true);
    expect(roleCan("viewer", "search:query")).toBe(true);
    expect(roleCan("viewer", "chat:use")).toBe(true);
    expect(roleCan("viewer", "document:create")).toBe(false);
    expect(roleCan("viewer", "document:delete")).toBe(false);
  });

  it("lets a member manage documents but not members", () => {
    expect(roleCan("member", "document:create")).toBe(true);
    expect(roleCan("member", "member:invite")).toBe(false);
    expect(roleCan("member", "workspace:update")).toBe(false);
  });

  it("gives an admin member management but not deletion of the workspace", () => {
    expect(roleCan("admin", "member:invite")).toBe(true);
    expect(roleCan("admin", "workspace:update")).toBe(true);
    expect(roleCan("admin", "workspace:delete")).toBe(false);
  });

  it("gives the owner everything", () => {
    expect(roleCan("owner", "workspace:delete")).toBe(true);
  });

  it("fails closed for a missing or unrecognised role", () => {
    expect(roleCan(null, "document:read")).toBe(false);
    expect(roleCan(undefined, "document:read")).toBe(false);
    expect(roleCan("superuser" as never, "document:read")).toBe(false);
  });
});
