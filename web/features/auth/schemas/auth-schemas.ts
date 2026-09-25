import { z } from "zod";

/**
 * Form validation for the auth screens.
 *
 * These mirror the backend's rules closely enough to give instant feedback, but
 * they are not the authority: the server re-validates everything and its field
 * errors are applied to the form when it disagrees (`applyServerErrors`). The
 * password rule is deliberately length-only -- composition rules push people toward
 * predictable substitutions, and the backend documents the same stance.
 */

export const MIN_PASSWORD_LENGTH = 12;
export const MAX_PASSWORD_LENGTH = 256;

// Same permissive shape the backend accepts: anything@anything.tld. Confirmation
// email is the real validator, so this only rejects what is plainly not an address.
const email = z
  .string()
  .trim()
  .min(1, "Enter your email address.")
  .max(320, "That email address is too long.")
  .regex(/^[^\s@]+@[^\s@]+\.[^\s@]+$/, "Enter a valid email address.");

const newPassword = z
  .string()
  .min(MIN_PASSWORD_LENGTH, `Use at least ${MIN_PASSWORD_LENGTH} characters.`)
  .max(MAX_PASSWORD_LENGTH, `Use no more than ${MAX_PASSWORD_LENGTH} characters.`);

export const loginSchema = z.object({
  email,
  password: z.string().min(1, "Enter your password."),
});
export type LoginValues = z.infer<typeof loginSchema>;

export const registerSchema = z.object({
  full_name: z.string().trim().min(1, "Enter your name.").max(200, "Use 200 characters or fewer."),
  email,
  password: newPassword,
});
export type RegisterValues = z.infer<typeof registerSchema>;

export const forgotPasswordSchema = z.object({ email });
export type ForgotPasswordValues = z.infer<typeof forgotPasswordSchema>;

export const resetPasswordSchema = z
  .object({
    new_password: newPassword,
    confirm_password: z.string().min(1, "Re-enter the new password."),
  })
  .refine((values) => values.new_password === values.confirm_password, {
    path: ["confirm_password"],
    message: "The passwords don't match.",
  });
export type ResetPasswordValues = z.infer<typeof resetPasswordSchema>;
