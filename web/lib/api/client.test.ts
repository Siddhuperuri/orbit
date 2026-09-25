import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, buildQuery, fillPath } from "@/lib/api/client";
import { ApiError, ErrorCode, isApiError } from "@/lib/api/errors";
import { onSessionEnded, resetSessionStateForTests } from "@/lib/api/session";

/**
 * These stub `fetch` at the network boundary -- the only honest seam for
 * asserting what goes on the wire. Behaviour against the real backend is checked
 * separately, end to end.
 */

function json(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

function envelope(code: string, status: number, extra: Partial<{ details: unknown }> = {}) {
  return json(
    { error: { code, message: `msg for ${code}`, request_id: "req-1", details: null, ...extra } },
    { status },
  );
}

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  resetSessionStateForTests();
  window.localStorage.clear();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function lastCall(index = fetchMock.mock.calls.length - 1) {
  const [url, init] = fetchMock.mock.calls[index]!;
  return { url: String(url), init: init as RequestInit, headers: new Headers(init?.headers) };
}

describe("path and query building", () => {
  it("encodes path parameters", () => {
    expect(fillPath("/a/{id}/b", { id: "x y/z" })).toBe("/a/x%20y%2Fz/b");
  });

  it("refuses a missing path parameter rather than requesting a broken URL", () => {
    expect(() => fillPath("/a/{id}")).toThrow(/Missing path parameter "id"/);
  });

  it("omits undefined and null, repeats array keys", () => {
    expect(buildQuery({ a: 1, b: undefined, c: null, d: ["x", "y"], e: false })).toBe(
      "?a=1&d=x&d=y&e=false",
    );
    expect(buildQuery({})).toBe("");
  });
});

describe("requests", () => {
  it("sends credentials, accepts JSON, and does not add the CSRF header to a read", async () => {
    fetchMock.mockResolvedValueOnce(json({ id: "u1" }));
    await api.get("/api/v1/auth/me");

    const { url, init, headers } = lastCall();
    expect(url).toBe("/api/v1/auth/me");
    expect(init.credentials).toBe("same-origin");
    expect(init.method).toBe("GET");
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.has("X-Requested-With")).toBe(false);
  });

  it("serialises a JSON body and marks mutations with X-Requested-With", async () => {
    fetchMock.mockResolvedValueOnce(json({ id: "w" }, { status: 201 }));
    await api.post("/api/v1/workspaces", { json: { name: "Research" } });

    const { init, headers } = lastCall();
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ name: "Research" }));
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("X-Requested-With")).toBe("orbit-web");
  });

  it("builds path and query from the route template", async () => {
    fetchMock.mockResolvedValueOnce(json({ items: [], next_cursor: null, has_more: false }));
    await api.get("/api/v1/workspaces/{workspace_id}/documents", {
      path: { workspace_id: "ws 1" },
      query: { limit: 25, status: "ready", cursor: undefined },
    });
    expect(lastCall().url).toBe("/api/v1/workspaces/ws%201/documents?limit=25&status=ready");
  });

  it("resolves undefined for an empty body (204 and 202)", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(api.post("/api/v1/auth/logout", { refresh: false })).resolves.toBeUndefined();

    fetchMock.mockResolvedValueOnce(new Response("", { status: 202 }));
    await expect(
      api.post("/api/v1/auth/password-reset", { json: { email: "a@b.co" }, refresh: false }),
    ).resolves.toBeUndefined();
  });
});

describe("errors", () => {
  it("parses the envelope into a typed ApiError", async () => {
    fetchMock.mockResolvedValueOnce(
      json(
        {
          error: {
            code: "VALIDATION_ERROR",
            message: "The request contains invalid values.",
            request_id: "req-9",
            details: [{ field: "body.email", message: "Enter a valid email address." }],
          },
        },
        { status: 422, headers: { "Retry-After": "7" } },
      ),
    );

    const error = await api.get("/api/v1/auth/me", { refresh: false }).catch((e: unknown) => e);
    expect(isApiError(error)).toBe(true);
    const apiError = error as ApiError;
    expect(apiError.code).toBe(ErrorCode.Validation);
    expect(apiError.requestId).toBe("req-9");
    expect(apiError.retryAfterSeconds).toBe(7);
    expect(apiError.fieldErrors()).toEqual({ email: "Enter a valid email address." });
    expect(apiError.isRetryable).toBe(false);
  });

  it("treats a proxy's HTML 502 as a retryable outage, not an internal error", async () => {
    fetchMock.mockResolvedValueOnce(new Response("<html>Bad gateway</html>", { status: 502 }));
    const error = (await api.get("/api/v1/meta").catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe(ErrorCode.DependencyUnavailable);
    expect(error.isRetryable).toBe(true);
  });

  it("maps a network failure to NETWORK_UNREACHABLE", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const error = (await api.get("/api/v1/meta").catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe(ErrorCode.NetworkUnreachable);
    expect(error.isRetryable).toBe(true);
  });

  it("lets a caller's abort through untouched", async () => {
    fetchMock.mockRejectedValueOnce(new DOMException("aborted", "AbortError"));
    const error = await api.get("/api/v1/meta").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(DOMException);
  });
});

describe("transparent session refresh", () => {
  it("refreshes once and replays the request", async () => {
    fetchMock
      .mockResolvedValueOnce(envelope("AUTHENTICATION_REQUIRED", 401))
      .mockResolvedValueOnce(json({ user: { id: "u" } })) // /auth/refresh
      .mockResolvedValueOnce(json({ id: "u", full_name: "Ada" }));

    const me = await api.get("/api/v1/auth/me");

    expect(me).toEqual({ id: "u", full_name: "Ada" });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(lastCall(1).url).toBe("/api/v1/auth/refresh");
    expect(lastCall(1).init.method).toBe("POST");
  });

  it("shares ONE refresh between concurrent failures (reuse detection would revoke the session)", async () => {
    let refreshCalls = 0;
    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/refresh")) {
        refreshCalls += 1;
        await new Promise((resolve) => setTimeout(resolve, 20));
        return json({ user: {} });
      }
      // Fails until the refresh has happened, then succeeds.
      return refreshCalls === 0 ? envelope("AUTHENTICATION_REQUIRED", 401) : json({ ok: true });
    });

    const results = await Promise.all([
      api.get("/api/v1/auth/me"),
      api.get("/api/v1/workspaces"),
      api.get("/api/v1/meta"),
    ]);

    expect(results).toHaveLength(3);
    expect(refreshCalls).toBe(1);
  });

  it("skips the refresh when another tab already refreshed after this request was sent", async () => {
    fetchMock
      .mockImplementationOnce(async () => {
        // Another tab finishes its refresh while this request is in flight.
        window.localStorage.setItem("orbit:session:refreshed-at", String(Date.now() + 1));
        return envelope("AUTHENTICATION_REQUIRED", 401);
      })
      .mockResolvedValueOnce(json({ id: "u" }));

    await api.get("/api/v1/auth/me");

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/auth/refresh"))).toBe(false);
  });

  it("ends the session when the refresh token is no longer valid", async () => {
    const ended = vi.fn();
    onSessionEnded(ended);
    fetchMock
      .mockResolvedValueOnce(envelope("AUTHENTICATION_REQUIRED", 401))
      .mockResolvedValueOnce(envelope("AUTHENTICATION_REQUIRED", 401));

    const error = (await api.get("/api/v1/auth/me").catch((e: unknown) => e)) as ApiError;

    expect(error.code).toBe(ErrorCode.AuthenticationRequired);
    expect(ended).toHaveBeenCalledExactlyOnceWith("expired");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("announces the end of a session once, however many requests fail", async () => {
    const ended = vi.fn();
    onSessionEnded(ended);
    fetchMock.mockImplementation(async () => envelope("AUTHENTICATION_REQUIRED", 401));

    await Promise.allSettled([api.get("/api/v1/auth/me"), api.get("/api/v1/workspaces")]);

    expect(ended).toHaveBeenCalledTimes(1);
  });

  it("does not sign the user out when the refresh itself fails transiently", async () => {
    const ended = vi.fn();
    onSessionEnded(ended);
    fetchMock
      .mockResolvedValueOnce(envelope("AUTHENTICATION_REQUIRED", 401))
      .mockResolvedValueOnce(envelope("DEPENDENCY_UNAVAILABLE", 503));

    const error = (await api.get("/api/v1/auth/me").catch((e: unknown) => e)) as ApiError;

    expect(error.code).toBe(ErrorCode.DependencyUnavailable);
    expect(ended).not.toHaveBeenCalled();
  });

  it("never refreshes for a wrong password", async () => {
    fetchMock.mockResolvedValueOnce(envelope("INVALID_CREDENTIALS", 401));
    const error = (await api
      .post("/api/v1/auth/login", { json: { email: "a@b.co", password: "x" }, refresh: false })
      .catch((e: unknown) => e)) as ApiError;

    expect(error.code).toBe(ErrorCode.InvalidCredentials);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not refresh when a call opts out", async () => {
    fetchMock.mockResolvedValueOnce(envelope("AUTHENTICATION_REQUIRED", 401));
    await api.post("/api/v1/auth/logout", { refresh: false }).catch(() => undefined);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
