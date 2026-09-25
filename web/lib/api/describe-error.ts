import { ErrorCode, isApiError } from "@/lib/api/errors";

/**
 * Turns any thrown value into something a person can act on.
 *
 * This is the one place error *presentation* is decided (ADR-0014: clients branch
 * on `code`, never on message text). Toasts, inline error states, and form-level
 * errors all render from the same description, so a class of failure reads the
 * same wherever it surfaces.
 *
 * Server messages are used verbatim for client errors -- the backend writes them
 * for users -- but never for internal errors, where the only thing worth showing
 * is the request id support can search for.
 */

export type ErrorKind =
  | "offline"
  | "session"
  | "forbidden"
  | "not-found"
  | "invalid"
  | "conflict"
  | "rate-limited"
  | "unavailable"
  | "internal";

export interface ErrorDescription {
  kind: ErrorKind;
  title: string;
  message: string;
  /** Present when the server produced one; the handle support uses to find the incident. */
  requestId: string;
  /** Whether offering a retry makes sense. */
  retryable: boolean;
  retryAfterSeconds: number | null;
}

const INTERNAL_MESSAGE =
  "Something went wrong on our side. If it keeps happening, quote the reference below to support.";

function waitHint(seconds: number | null): string {
  if (seconds === null) return "Wait a moment and try again.";
  if (seconds < 60) return `Try again in ${seconds} second${seconds === 1 ? "" : "s"}.`;
  const minutes = Math.ceil(seconds / 60);
  return `Try again in about ${minutes} minute${minutes === 1 ? "" : "s"}.`;
}

export function describeError(error: unknown): ErrorDescription {
  if (!isApiError(error)) {
    return {
      kind: "internal",
      title: "Something went wrong",
      message: INTERNAL_MESSAGE,
      requestId: "",
      retryable: false,
      retryAfterSeconds: null,
    };
  }

  const base = {
    requestId: error.requestId,
    retryAfterSeconds: error.retryAfterSeconds,
    retryable: error.isRetryable,
  };

  switch (error.code) {
    case ErrorCode.NetworkUnreachable:
      return {
        ...base,
        kind: "offline",
        title: "Can't reach ORBIT",
        message: "Check your connection, then try again.",
      };
    case ErrorCode.AuthenticationRequired:
      return {
        ...base,
        kind: "session",
        title: "Your session has ended",
        message: "Sign in again to continue.",
        retryable: false,
      };
    case ErrorCode.PermissionDenied:
      return {
        ...base,
        kind: "forbidden",
        title: "You don't have permission",
        message: error.message,
        retryable: false,
      };
    case ErrorCode.NotFound:
      return {
        ...base,
        kind: "not-found",
        title: "Not found",
        message: "It may have been deleted, or you may not have access to it.",
        retryable: false,
      };
    case ErrorCode.Validation:
    case ErrorCode.BadRequest:
    case ErrorCode.UploadTooLarge:
    case ErrorCode.UnsupportedContentType:
      return {
        ...base,
        kind: "invalid",
        title: "That couldn't be done",
        message: error.message,
        retryable: false,
      };
    case ErrorCode.Conflict:
    case ErrorCode.AnswerInProgress:
      return {
        ...base,
        kind: "conflict",
        title: "That's changed",
        message: error.message,
        retryable: false,
      };
    case ErrorCode.RateLimited:
    case ErrorCode.GenerationRateLimited:
      return {
        ...base,
        kind: "rate-limited",
        title: "Too many requests",
        message: waitHint(error.retryAfterSeconds),
        retryable: true,
      };
    default:
      break;
  }

  // A defect is our fault and says nothing useful; only its reference does.
  if (error.code === ErrorCode.Internal) {
    return { ...base, kind: "internal", title: "Something went wrong", message: INTERNAL_MESSAGE };
  }

  // A transient dependency failure: the request was fine, the dependency was not.
  if (error.isRetryable) {
    return {
      ...base,
      kind: "unavailable",
      title: "Temporarily unavailable",
      message: "ORBIT couldn't complete that just now. Try again in a moment.",
    };
  }

  return { ...base, kind: "invalid", title: "That couldn't be done", message: error.message };
}
