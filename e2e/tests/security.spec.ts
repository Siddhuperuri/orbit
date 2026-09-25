/**
 * The boundary tests, against the running stack.
 *
 * The backend suite already proves these at the repository and use-case level
 * (`backend/tests/integration/test_tenant_isolation.py`). What it cannot prove
 * is that the *deployed path* preserves them: the router, its dependencies, the
 * cookie scoping, and the Next rewrite in front of all of it. That is what these
 * assert, over HTTP, exactly as an attacker would reach them.
 *
 * Note the expected status codes. A resource belonging to another tenant reads
 * as **404, not 403**: a 403 would confirm the id exists and is therefore itself
 * the leak. Where a test asserts 404 below, that is the point of the test.
 */

import type { Browser } from "@playwright/test";

import { webBaseUrl } from "../env";
import { newCredentials, OrbitApi, REQUESTED_WITH } from "../fixtures/api";
import { buildPdf, handbookPages, handbookTitle } from "../fixtures/pdf";
import { expect, test } from "../fixtures/orbit";

/** A second, unrelated tenant with a workspace and a document of their own. */
async function otherTenant(
  browser: Browser,
): Promise<{ api: OrbitApi; workspaceId: string; documentId: string }> {
  const api = await OrbitApi.signedUp(browser, newCredentials("outsider"));
  const workspaceId = await api.createWorkspace("Their Workspace");
  const documentId = await api.upload(workspaceId, {
    filename: "theirs.pdf",
    contentType: "application/pdf",
    body: buildPdf(handbookPages, { title: handbookTitle }),
  });
  return { api, workspaceId, documentId };
}

test.describe("cross-tenant access", () => {
  test("another tenant's workspace is not readable, writable, or deletable", async ({
    api,
    browser,
  }) => {
    const them = await otherTenant(browser);

    for (const [method, path] of [
      ["get", `/api/v1/workspaces/${them.workspaceId}`],
      ["get", `/api/v1/workspaces/${them.workspaceId}/documents`],
      ["get", `/api/v1/workspaces/${them.workspaceId}/members`],
      ["get", `/api/v1/workspaces/${them.workspaceId}/folders`],
      ["get", `/api/v1/workspaces/${them.workspaceId}/tags`],
      ["get", `/api/v1/workspaces/${them.workspaceId}/conversations`],
    ] as const) {
      const response = await api[method](path);
      expect(response.status(), `${method.toUpperCase()} ${path}`).toBe(404);
    }

    const renamed = await api.patch(`/api/v1/workspaces/${them.workspaceId}`, {
      headers: REQUESTED_WITH,
      data: { name: "Hijacked", version: 1 },
    });
    expect(renamed.status()).toBe(404);

    const deleted = await api.delete(`/api/v1/workspaces/${them.workspaceId}`, {
      headers: REQUESTED_WITH,
    });
    expect(deleted.status()).toBe(404);

    await them.api.dispose();
  });

  test("another tenant's document is not reachable by its id (IDOR)", async ({
    api,
    workspace,
    browser,
  }) => {
    const them = await otherTenant(browser);

    // Their id, *their* workspace in the path: the direct object reference.
    const direct = await api.get(
      `/api/v1/workspaces/${them.workspaceId}/documents/${them.documentId}`,
    );
    expect(direct.status()).toBe(404);

    // Their id smuggled into a workspace the caller *does* own. This is the
    // interesting one: authorisation on the workspace succeeds, so only a
    // document lookup that is itself workspace-scoped refuses it.
    const smuggled = await api.get(
      `/api/v1/workspaces/${workspace.id}/documents/${them.documentId}`,
    );
    expect(smuggled.status()).toBe(404);

    // The same for every side door out of a document.
    for (const suffix of ["/content", "/download", "/processing", "/versions"]) {
      const response = await api.get(
        `/api/v1/workspaces/${workspace.id}/documents/${them.documentId}${suffix}`,
      );
      expect(response.status(), suffix).toBe(404);
    }

    await them.api.dispose();
  });

  test("another tenant's document cannot be modified or destroyed", async ({
    api,
    workspace,
    browser,
  }) => {
    const them = await otherTenant(browser);
    const theirs = `/api/v1/workspaces/${workspace.id}/documents/${them.documentId}`;

    const patched = await api.patch(theirs, {
      headers: REQUESTED_WITH,
      data: { title: "Hijacked", expected_version: 1 },
    });
    expect(patched.status()).toBe(404);

    const archived = await api.post(`${theirs}/archive`, {
      headers: REQUESTED_WITH,
    });
    expect(archived.status()).toBe(404);

    const reprocessed = await api.post(`${theirs}/reprocess`, {
      headers: REQUESTED_WITH,
    });
    expect(reprocessed.status()).toBe(404);

    const removed = await api.delete(theirs, { headers: REQUESTED_WITH });
    expect(removed.status()).toBe(404);

    // And it is still there, for them.
    const stillTheirs = await them.api.document(them.workspaceId, them.documentId);
    expect(stillTheirs.id).toBe(them.documentId);

    await them.api.dispose();
  });

  test("search never crosses the workspace boundary", async ({ api, workspace, browser }) => {
    const them = await otherTenant(browser);
    await them.api.waitForReady(them.workspaceId, them.documentId);

    // The caller's own workspace is empty, so a term that exists only in the
    // other tenant's document must return nothing -- not a permission error,
    // simply no results, which is the truthful answer for this workspace.
    const mine = (await api.search(workspace.id, "flowmeter")) as {
      results: unknown[];
    };
    expect(mine.results).toHaveLength(0);

    // And searching *their* workspace is refused outright.
    const theirs = await api.post(`/api/v1/workspaces/${them.workspaceId}/search`, {
      headers: REQUESTED_WITH,
      data: { query: "flowmeter" },
    });
    expect(theirs.status()).toBe(404);

    await them.api.dispose();
  });
});

test.describe("unauthenticated access", () => {
  test("every workspace-scoped route refuses an anonymous caller", async ({
    workspace,
    browser,
  }) => {
    const anonymous = await OrbitApi.anonymous(browser);

    for (const path of [
      `/api/v1/workspaces`,
      `/api/v1/workspaces/${workspace.id}`,
      `/api/v1/workspaces/${workspace.id}/documents`,
      `/api/v1/workspaces/${workspace.id}/folders`,
      `/api/v1/workspaces/${workspace.id}/tags`,
      `/api/v1/workspaces/${workspace.id}/conversations`,
      `/api/v1/auth/me`,
    ]) {
      const response = await anonymous.get(path);
      expect(response.status(), path).toBe(401);
    }

    await anonymous.dispose();
  });

  test("a forged session cookie is rejected", async ({ workspace, browser }) => {
    const anonymous = await OrbitApi.anonymous(browser);
    const response = await anonymous.get(`/api/v1/workspaces/${workspace.id}`, {
      // A syntactically plausible token that was never signed by this server.
      headers: { Cookie: "orbit_access=not.a.real.token" },
    });
    expect(response.status()).toBe(401);

    await anonymous.dispose();
  });
});

test.describe("session handling", () => {
  test("signing out revokes the session for every later request", async ({ account, browser }) => {
    const api = await OrbitApi.signedUp(browser, account);
    const workspaceId = await api.createWorkspace("Soon To Be Inaccessible");

    expect((await api.get(`/api/v1/workspaces/${workspaceId}`)).status()).toBe(200);

    const out = await api.post("/api/v1/auth/logout", {
      headers: REQUESTED_WITH,
    });
    expect(out.status()).toBe(204);

    // The cookie is gone from the jar, and -- the part that matters -- replaying
    // it would not help either, which the refresh test below covers.
    expect((await api.get(`/api/v1/workspaces/${workspaceId}`)).status()).toBe(401);
    await api.dispose();
  });

  test("a refresh token cannot be replayed after it has been used", async ({
    account,
    browser,
  }) => {
    const api = await OrbitApi.signedUp(browser, account);

    // Capture the refresh cookie before spending it.
    const refresh = api.cookie("orbit_refresh");
    expect(refresh, "expected a refresh cookie after login").toBeTruthy();

    const first = await api.post("/api/v1/auth/refresh", {
      headers: REQUESTED_WITH,
    });
    expect(first.status()).toBe(200);

    // Replaying the *old* token is the classic stolen-cookie case. Rotation
    // means it is already spent; a server that accepted it would let a copied
    // cookie live forever.
    const replayed = await api.post("/api/v1/auth/refresh", {
      headers: { ...REQUESTED_WITH, Cookie: `orbit_refresh=${refresh}` },
    });
    expect(replayed.status()).toBe(401);

    await api.dispose();
  });
});

test.describe("request shape", () => {
  test("a cookie-authenticated mutation from another origin is refused (CSRF)", async ({ api }) => {
    // The control is the `Origin` header (ADR-0009): `SameSite=Lax` is the
    // primary defence, and this middleware is the independent second layer. A
    // cross-site form post carries the cookie and its own origin -- which is
    // exactly what is forged here.
    const forged = await api.post("/api/v1/workspaces", {
      data: { name: "Cross-site" },
      headers: { Origin: "https://evil.example" },
    });
    expect(forged.status()).toBe(403);
    expect(JSON.parse(await forged.text()).error.code).toBe("CSRF_ORIGIN_MISMATCH");

    // A near-miss is still a miss: origins compare as whole strings, so a
    // different scheme or port does not vouch for this one.
    for (const origin of ["http://127.0.0.1:9999", "https://127.0.0.1:3100"]) {
      const response = await api.post("/api/v1/workspaces", {
        data: { name: "Near miss" },
        headers: { Origin: origin },
      });
      expect(response.status(), origin).toBe(403);
    }

    // And the app's own origin is accepted, so the rule is not simply "refuse
    // everything with an Origin" -- a test that passed either way.
    const legitimate = await api.post("/api/v1/workspaces", {
      data: { name: "Same origin" },
      headers: { Origin: webBaseUrl },
    });
    expect(legitimate.status()).toBe(201);
  });

  test("malformed and oversized request bodies are refused, not crashed on", async ({
    api,
    workspace,
  }) => {
    // Not JSON at all.
    const garbage = await api.post("/api/v1/workspaces", {
      headers: { ...REQUESTED_WITH, "Content-Type": "application/json" },
      data: "{not json",
    });
    expect(garbage.status()).toBe(422);

    // Right shape, impossible values.
    for (const body of [{ name: "" }, { name: "x".repeat(10_000) }, { name: null }, {}]) {
      const response = await api.post("/api/v1/workspaces", {
        headers: REQUESTED_WITH,
        data: body,
      });
      expect([400, 422], JSON.stringify(body)).toContain(response.status());
    }

    // A uuid-shaped path segment that is not a uuid.
    const notAUuid = await api.get(`/api/v1/workspaces/not-a-uuid`);
    expect(notAUuid.status()).toBe(422);

    // A well-formed uuid that is simply nobody's.
    const nobodys = await api.get(`/api/v1/workspaces/00000000-0000-7000-8000-000000000000`);
    expect(nobodys.status()).toBe(404);

    // A search query far beyond the documented ceiling.
    const huge = await api.post(`/api/v1/workspaces/${workspace.id}/search`, {
      headers: REQUESTED_WITH,
      data: { query: "a".repeat(50_000) },
    });
    expect(huge.status()).toBe(422);
  });

  test("an error never echoes internals back to the caller", async ({ api }) => {
    const response = await api.get(`/api/v1/workspaces/00000000-0000-7000-8000-000000000000`);
    const body = await response.text();

    // The envelope is the contract: a code, a message, a request id.
    const payload = JSON.parse(body);
    expect(payload.error.code).toBe("NOT_FOUND");
    expect(payload.error.request_id).toMatch(/^[0-9A-HJKMNP-TV-Z]{26}$/);

    // And nothing else.
    for (const leak of ["Traceback", "sqlalchemy", "asyncpg", "SELECT ", "/src/orbit"]) {
      expect(body, leak).not.toContain(leak);
    }
  });
});

test.describe("rate limiting", () => {
  test("repeated failed logins are throttled rather than answered forever", async ({
    account,
    browser,
  }) => {
    const api = await OrbitApi.signedUp(browser, account);
    const attacker = await OrbitApi.anonymous(browser);

    // Guess the same account's password repeatedly. The limiter is per account
    // and per address, so this is the shape of a real credential-stuffing run.
    let throttled = false;
    for (let attempt = 0; attempt < 25; attempt += 1) {
      const response = await attacker.post("/api/v1/auth/login", {
        headers: REQUESTED_WITH,
        data: { email: account.email, password: `wrong-guess-${attempt}` },
      });
      if (response.status() === 429) {
        throttled = true;
        // A throttle that does not say when to come back is not usable by a
        // well-behaved client, and is how a retry storm starts.
        expect(response.headers()["retry-after"]).toBeTruthy();
        break;
      }
      expect(response.status(), "a wrong password must not succeed").toBe(401);
    }
    expect(throttled, "brute force was never throttled").toBe(true);

    await attacker.dispose();
    await api.dispose();
  });
});
