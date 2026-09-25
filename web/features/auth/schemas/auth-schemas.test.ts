import { describe, expect, it } from "vitest";

import {
  forgotPasswordSchema,
  loginSchema,
  registerSchema,
  resetPasswordSchema,
} from "@/features/auth/schemas/auth-schemas";

function firstMessage(result: {
  success: boolean;
  error?: { issues: Array<{ message: string }> };
}) {
  return result.success ? null : (result.error?.issues[0]?.message ?? null);
}

describe("registerSchema", () => {
  const valid = { full_name: "Ada Lovelace", email: "ada@example.com", password: "twelve-chars!" };

  it("accepts a valid registration and trims the name and email", () => {
    const parsed = registerSchema.parse({
      ...valid,
      full_name: "  Ada  ",
      email: "  ada@example.com ",
    });
    expect(parsed.full_name).toBe("Ada");
    expect(parsed.email).toBe("ada@example.com");
  });

  it("enforces the backend's 12-character minimum with a message that says so", () => {
    expect(firstMessage(registerSchema.safeParse({ ...valid, password: "short" }))).toBe(
      "Use at least 12 characters.",
    );
  });

  it("does not impose composition rules the backend deliberately omits", () => {
    expect(registerSchema.safeParse({ ...valid, password: "alllowercaseletters" }).success).toBe(
      true,
    );
  });

  it.each(["", "no-at-sign", "a@b", "a b@c.co", "@c.co"])("rejects the email %j", (email) => {
    expect(registerSchema.safeParse({ ...valid, email }).success).toBe(false);
  });

  it("rejects a name that is only whitespace", () => {
    expect(registerSchema.safeParse({ ...valid, full_name: "   " }).success).toBe(false);
  });
});

describe("loginSchema", () => {
  it("only requires a password to be present -- it must not reveal the length rule", () => {
    expect(loginSchema.safeParse({ email: "a@b.co", password: "x" }).success).toBe(true);
    expect(loginSchema.safeParse({ email: "a@b.co", password: "" }).success).toBe(false);
  });
});

describe("resetPasswordSchema", () => {
  it("attaches the mismatch to the confirmation field", () => {
    const result = resetPasswordSchema.safeParse({
      new_password: "twelve-chars!",
      confirm_password: "different-pass!",
    });
    expect(result.success).toBe(false);
    expect(result.error?.issues[0]?.path).toEqual(["confirm_password"]);
  });

  it("passes when they match", () => {
    expect(
      resetPasswordSchema.safeParse({
        new_password: "twelve-chars!",
        confirm_password: "twelve-chars!",
      }).success,
    ).toBe(true);
  });
});

describe("forgotPasswordSchema", () => {
  it("requires an email", () => {
    expect(forgotPasswordSchema.safeParse({ email: "" }).success).toBe(false);
  });
});
