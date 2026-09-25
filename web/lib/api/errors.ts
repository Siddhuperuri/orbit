import { z } from "zod";

/**
 * The single place the backend's error envelope is parsed.
 *
 * Every consumer branches on `code`, never on `message`: codes are a stable API
 * contract, while messages are human-facing copy that may be reworded or
 * localised at any time. String-matching a message is how a UI silently stops
 * handling an error class.
 *
 * See docs/decisions/0014-error-handling-strategy.md.
 */

const fieldErrorSchema = z.object({
  field: z.string(),
  message: z.string(),
});

const errorEnvelopeSchema = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    request_id: z.string(),
    details: z.array(fieldErrorSchema).nullable().optional(),
  }),
});

export type FieldError = z.infer<typeof fieldErrorSchema>;

/** Codes the UI reacts to structurally rather than merely displaying. */
export const ErrorCode = {
  AuthenticationRequired: "AUTHENTICATION_REQUIRED",
  InvalidCredentials: "INVALID_CREDENTIALS",
  PermissionDenied: "PERMISSION_DENIED",
  NotFound: "NOT_FOUND",
  Validation: "VALIDATION_ERROR",
  Conflict: "CONFLICT",
  BadRequest: "BAD_REQUEST",
  RateLimited: "RATE_LIMITED",
  UploadTooLarge: "UPLOAD_TOO_LARGE",
  UnsupportedContentType: "UNSUPPORTED_CONTENT_TYPE",
  CsrfOriginMismatch: "CSRF_ORIGIN_MISMATCH",
  DependencyUnavailable: "DEPENDENCY_UNAVAILABLE",
  Internal: "INTERNAL_ERROR",

  // Question answering: which stage failed decides the wording (ADR-0022).
  AnswerInProgress: "ANSWER_IN_PROGRESS",
  RetrievalFailed: "RETRIEVAL_FAILED",
  GenerationFailed: "GENERATION_FAILED",
  GenerationTimeout: "GENERATION_TIMEOUT",
  GenerationRateLimited: "GENERATION_RATE_LIMITED",
  ConversationUnavailable: "CONVERSATION_UNAVAILABLE",

  /** Synthesised locally when the network never delivered a response. */
  NetworkUnreachable: "NETWORK_UNREACHABLE",
  /** Synthesised locally when the caller cancelled the request. */
  Aborted: "REQUEST_ABORTED",
} as const;

export type ErrorCodeValue = (typeof ErrorCode)[keyof typeof ErrorCode];

/** Transient dependency codes: the request was fine, the dependency was not. */
const RETRYABLE_CODES: ReadonlySet<string> = new Set([
  ErrorCode.RateLimited,
  ErrorCode.DependencyUnavailable,
  ErrorCode.NetworkUnreachable,
  ErrorCode.RetrievalFailed,
  ErrorCode.GenerationFailed,
  ErrorCode.GenerationTimeout,
  ErrorCode.GenerationRateLimited,
  ErrorCode.ConversationUnavailable,
  "STORAGE_UNAVAILABLE",
  "DATABASE_UNAVAILABLE",
  "QUEUE_UNAVAILABLE",
  "AI_PROVIDER_UNAVAILABLE",
  "AI_PROVIDER_TIMEOUT",
  "AI_PROVIDER_RATE_LIMITED",
  "AI_PROVIDER_CIRCUIT_OPEN",
  "AI_PROVIDER_RESPONSE_INVALID",
]);

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId: string;
  readonly details: FieldError[];
  /** Seconds to wait before retrying, from `Retry-After`, when the server sent one. */
  readonly retryAfterSeconds: number | null;

  constructor(init: {
    code: string;
    message: string;
    status: number;
    requestId: string;
    details?: FieldError[] | null;
    retryAfterSeconds?: number | null;
  }) {
    super(init.message);
    this.name = "ApiError";
    this.code = init.code;
    this.status = init.status;
    this.requestId = init.requestId;
    this.details = init.details ?? [];
    this.retryAfterSeconds = init.retryAfterSeconds ?? null;
  }

  /** Whether retrying the identical request could plausibly succeed. */
  get isRetryable(): boolean {
    return this.status >= 500 || RETRYABLE_CODES.has(this.code);
  }

  /** Whether the session is gone and the user must sign in again. */
  get requiresAuthentication(): boolean {
    return this.code === ErrorCode.AuthenticationRequired;
  }

  /** Field errors keyed by their field path, for form binding. */
  fieldErrors(): Record<string, string> {
    const result: Record<string, string> = {};
    for (const detail of this.details) {
      // The backend prefixes paths with the request part ("body.email"); forms
      // address fields by their own names.
      const key = detail.field.replace(/^(body|query|path)\./, "");
      result[key] ??= detail.message;
    }
    return result;
  }
}

function parseRetryAfter(headers: Headers): number | null {
  const header = headers.get("Retry-After");
  if (header === null) return null;
  const seconds = Number.parseInt(header, 10);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

/** What an error response looks like once it has been read, whatever the transport. */
export interface RawErrorResponse {
  status: number;
  headers: Headers;
  /** The body as text; empty when there was none or it could not be read. */
  text: string;
}

// A reverse proxy answers 502/503/504 itself when the API is down, and its body
// is HTML, not our envelope. Those are still "the service is unavailable", and
// deserve the same retry treatment as a 503 the API sent deliberately.
const GATEWAY_STATUSES: ReadonlySet<number> = new Set([502, 503, 504]);

/**
 * Build an {@link ApiError} from a non-OK response.
 *
 * A failing response is exactly the case where the body may not be what the
 * contract promises -- a proxy 502 returns HTML, a dropped connection returns
 * nothing. Parsing is therefore defensive, and a malformed body still produces
 * a usable error rather than a second, confusing exception.
 *
 * Transport-neutral on purpose: `fetch` and `XMLHttpRequest` (used for upload
 * progress) both reduce to a {@link RawErrorResponse}, so the envelope is parsed
 * in exactly one place.
 */
export function apiErrorFromRaw(raw: RawErrorResponse): ApiError {
  const requestId = raw.headers.get("X-Request-ID") ?? "";
  const retryAfterSeconds = parseRetryAfter(raw.headers);

  let body: unknown;
  try {
    body = JSON.parse(raw.text);
  } catch {
    return unreadableError(raw.status, requestId, retryAfterSeconds);
  }

  const parsed = errorEnvelopeSchema.safeParse(body);
  if (!parsed.success) {
    return unreadableError(raw.status, requestId, retryAfterSeconds);
  }

  const { error } = parsed.data;
  return new ApiError({
    code: error.code,
    message: error.message,
    status: raw.status,
    // Prefer the body's id: it is what the server logged against.
    requestId: error.request_id || requestId,
    details: error.details ?? [],
    retryAfterSeconds,
  });
}

function unreadableError(
  status: number,
  requestId: string,
  retryAfterSeconds: number | null,
): ApiError {
  if (GATEWAY_STATUSES.has(status)) {
    return new ApiError({
      code: ErrorCode.DependencyUnavailable,
      message: "ORBIT is temporarily unavailable. Try again in a moment.",
      status,
      requestId,
      retryAfterSeconds,
    });
  }
  return new ApiError({
    code: ErrorCode.Internal,
    message: "The server returned an unexpected response.",
    status,
    requestId,
    retryAfterSeconds,
  });
}

export async function toApiError(response: Response): Promise<ApiError> {
  let text = "";
  try {
    text = await response.text();
  } catch {
    // An unreadable body is handled like an empty one: the status still tells us plenty.
  }
  return apiErrorFromRaw({ status: response.status, headers: response.headers, text });
}

/** The error used when the request never reached the server at all. */
export function networkError(cause: unknown): ApiError {
  const error = new ApiError({
    code: ErrorCode.NetworkUnreachable,
    message: "Could not reach the server. Check your connection and try again.",
    status: 0,
    requestId: "",
    retryAfterSeconds: null,
  });
  // Preserved for the developer console: it distinguishes offline from DNS
  // failure from an aborted request, none of which the user-facing message says.
  error.cause = cause;
  return error;
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

/** True for a cancellation the caller asked for: not a failure, and not worth reporting. */
export function isAbortError(value: unknown): boolean {
  return (
    (value instanceof DOMException && value.name === "AbortError") ||
    (isApiError(value) && value.code === ErrorCode.Aborted)
  );
}
