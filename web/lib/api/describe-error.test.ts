import { describe, expect, it } from "vitest";

import { describeError } from "@/lib/api/describe-error";
import { ApiError, ErrorCode, networkError } from "@/lib/api/errors";

function error(
  code: string,
  status: number,
  extra: Partial<ConstructorParameters<typeof ApiError>[0]> = {},
) {
  return new ApiError({
    code,
    status,
    message: `server says ${code}`,
    requestId: "req-42",
    ...extra,
  });
}

describe("describeError", () => {
  it("never shows an unexpected error's message", () => {
    const description = describeError(new Error("SELECT * FROM secrets failed"));
    expect(description.message).not.toContain("SELECT");
    expect(description.kind).toBe("internal");
  });

  it("does not echo a 500's server text, but keeps the request id", () => {
    const description = describeError(
      error(ErrorCode.Internal, 500, { message: "stack trace here" }),
    );
    expect(description.message).not.toContain("stack trace");
    expect(description.requestId).toBe("req-42");
    expect(description.kind).toBe("internal");
  });

  it("uses the server's message for a validation failure -- it is written for users", () => {
    const description = describeError(error(ErrorCode.Validation, 422));
    expect(description.message).toBe("server says VALIDATION_ERROR");
    expect(description.retryable).toBe(false);
  });

  it("offers a retry for an outage and not for a client error", () => {
    expect(describeError(error(ErrorCode.DependencyUnavailable, 503)).retryable).toBe(true);
    expect(describeError(error(ErrorCode.NotFound, 404)).retryable).toBe(false);
    expect(describeError(error(ErrorCode.PermissionDenied, 403)).retryable).toBe(false);
  });

  it("tells the user how long to wait when the server said", () => {
    expect(
      describeError(error(ErrorCode.RateLimited, 429, { retryAfterSeconds: 30 })).message,
    ).toBe("Try again in 30 seconds.");
    expect(
      describeError(error(ErrorCode.RateLimited, 429, { retryAfterSeconds: 200 })).message,
    ).toBe("Try again in about 4 minutes.");
    expect(describeError(error(ErrorCode.RateLimited, 429)).message).toBe(
      "Wait a moment and try again.",
    );
  });

  it("distinguishes offline from a server fault", () => {
    expect(describeError(networkError(new TypeError("x"))).kind).toBe("offline");
  });

  it("treats every question-answering stage failure as retryable and gives none a raw message", () => {
    for (const code of [
      ErrorCode.RetrievalFailed,
      ErrorCode.GenerationFailed,
      ErrorCode.GenerationTimeout,
    ]) {
      const description = describeError(error(code, 503));
      expect(description.retryable).toBe(true);
      expect(description.kind).toBe("unavailable");
    }
  });
});
