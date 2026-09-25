import type { FieldPath, FieldValues, UseFormSetError } from "react-hook-form";

import { describeError } from "@/lib/api/describe-error";
import { isApiError } from "@/lib/api/errors";

/**
 * Pushes the server's field-level validation errors onto a React Hook Form.
 *
 * Client-side Zod validation catches what it can before a request is made, but the
 * server is the authority (a rule can change, or depend on state the client cannot
 * see). Its `details` carry the offending field paths, so each message lands on
 * the field it belongs to -- inline, linked by `aria-describedby`.
 *
 * Returns the message for anything that could not be attached to a field (a
 * network failure, a 429, a field the form does not have), for the caller to show
 * as a form-level error. `null` means everything was placed on fields.
 */
export function applyServerErrors<T extends FieldValues>(
  error: unknown,
  setError: UseFormSetError<T>,
  fields: ReadonlyArray<FieldPath<T>>,
): { message: string; requestId: string } | null {
  if (!isApiError(error)) {
    return { message: describeError(error).message, requestId: "" };
  }

  const byField = error.fieldErrors();
  let placed = 0;
  for (const field of fields) {
    const message = byField[field];
    if (message) {
      setError(field, { type: "server", message });
      placed += 1;
    }
  }

  if (placed > 0 && placed === Object.keys(byField).length) return null;

  const description = describeError(error);
  return { message: description.message, requestId: description.requestId };
}
