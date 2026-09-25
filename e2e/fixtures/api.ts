/**
 * A direct HTTP client for the ORBIT API, used for two jobs the browser cannot do.
 *
 * **Arranging state.** A test about citations should not spend thirty seconds
 * driving the upload UI to get a document in place. It calls the API, and the
 * browser then starts from the state it cares about.
 *
 * **Attacking.** Most of `security.spec.ts` is about requests a browser would
 * never send: another tenant's document id, a missing CSRF header, a forged
 * cookie. Those are HTTP-level facts and are asserted at the HTTP level.
 *
 * Requests go through the *web* origin, not straight to the API port, because
 * that is the path a real client takes -- single origin, Next rewrite in front
 * (ADR-0009) -- and it is the one whose cookie scoping and proxy limits matter.
 */

import { APIRequestContext, APIResponse, Browser, BrowserContext, expect } from "@playwright/test";

import { webBaseUrl } from "../env";

/** What the verbs below accept; a narrow slice of Playwright's own options. */
export interface RequestOptions {
  headers?: Record<string, string>;
  data?: unknown;
  timeout?: number;
  /**
   * Whether to send `X-Requested-With`. True everywhere except the CSRF test,
   * which exists precisely to show what happens without it.
   */
  requestedWith?: boolean;
}

/** Sent on mutating requests; the web client sends it and the API requires it. */
export const REQUESTED_WITH = { "X-Requested-With": "orbit-web" };

export interface Credentials {
  email: string;
  password: string;
  fullName: string;
}

/**
 * A unique account per call.
 *
 * Unique, not random: the local part carries the test's own name and a counter,
 * so a row left behind in the database says which test made it. The password is
 * a constant -- it is not a secret, it is a fixture, and varying it would only
 * make a failure harder to reproduce.
 */
let accountCounter = 0;
export function newCredentials(label = "user"): Credentials {
  const slug = label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  accountCounter += 1;
  return {
    email: `e2e-${slug}-${process.pid}-${accountCounter}@orbit.test`,
    password: "an-entirely-ordinary-passphrase-42",
    fullName: `E2E ${label}`,
  };
}

/**
 * Both cookies are set `Secure` unconditionally (`api/cookies.py`), and that is
 * deliberate: a session cookie sent over plain HTTP is a credential in
 * cleartext. It does mean the session only survives in a jar that accepts it,
 * and over `http://127.0.0.1` Playwright's own jar does not: it applies the
 * `Secure` rule by the letter, with no exemption for loopback. Login returns
 * 200, the jar stays empty, and every later call is a 401 that reads like an
 * authorisation bug. Setting the header by hand does not help either --
 * Playwright owns the `Cookie` header and overwrites it from that same jar.
 *
 * So this client reads `Set-Cookie` off each response and puts the cookies into
 * the browser context's jar itself, with `secure: false` -- the one flag it is
 * allowed to choose, because here it is the client, not the server, deciding
 * what it will store. Everything else about the cookie is the server's: name,
 * value, path, and `httpOnly` are copied verbatim, so what is replayed is what
 * was issued.
 *
 * A consequence worth having: the jar belongs to the *browser context*, so a
 * page opened on that context is signed in as the same user, and a test can
 * move between HTTP calls and the UI without re-authenticating.
 */
export class OrbitApi {
  private readonly cookies = new Map<string, string>();
  private readonly transport: APIRequestContext;

  constructor(
    private readonly context: BrowserContext,
    private readonly ownsContext = false,
  ) {
    this.transport = context.request;
  }

  /** A client with no cookies at all -- an anonymous caller. */
  static async anonymous(browser: Browser): Promise<OrbitApi> {
    return new OrbitApi(await browser.newContext({ baseURL: webBaseUrl }), true);
  }

  /** A client holding a fresh account's session cookies. */
  static async signedUp(browser: Browser, credentials: Credentials): Promise<OrbitApi> {
    const api = await OrbitApi.anonymous(browser);
    await api.register(credentials);
    await api.login(credentials);
    return api;
  }

  async dispose(): Promise<void> {
    if (this.ownsContext) await this.context.close();
  }

  /** The cookie this client would send, or `undefined` if it holds none. */
  cookie(name: string): string | undefined {
    return this.cookies.get(name);
  }

  /** Forget a cookie, to model a client that lost or never received one. */
  forget(name: string): void {
    this.cookies.delete(name);
  }

  private async absorb(response: APIResponse): Promise<APIResponse> {
    const host = new URL(webBaseUrl).hostname;
    type Cookie = Parameters<BrowserContext["addCookies"]>[0][number];
    const add: Cookie[] = [];
    let cleared = false;

    for (const { name, value } of response.headersArray()) {
      if (name.toLowerCase() !== "set-cookie") continue;
      // One header per cookie. The name=value pair is everything before the
      // first `;`; an empty value, `Max-Age=0`, or any `Expires` is a deletion,
      // which is how the server clears a session on logout.
      const [pair = "", ...attributes] = value.split(";");
      const eq = pair.indexOf("=");
      if (eq <= 0) continue;
      const cookieName = pair.slice(0, eq).trim();
      const cookieValue = pair.slice(eq + 1).trim();
      const attribute = (key: string): string | undefined =>
        attributes
          .map((a) => a.trim())
          .find((a) => a.toLowerCase().startsWith(`${key}=`))
          ?.slice(key.length + 1);

      const deleted =
        cookieValue === "" ||
        /^0$/.test(attribute("max-age") ?? "") ||
        attribute("expires") !== undefined;

      if (deleted) {
        this.cookies.delete(cookieName);
        cleared = true;
        continue;
      }
      this.cookies.set(cookieName, cookieValue);
      add.push({
        name: cookieName,
        value: cookieValue,
        domain: host,
        path: attribute("path") ?? "/",
        httpOnly: attributes.some((a) => a.trim().toLowerCase() === "httponly"),
        // Ours to choose: see the note above the class.
        secure: false,
        sameSite: "Lax",
      });
    }

    // `clearCookies` takes no per-cookie filter that matches how the server
    // clears them, so a deletion is applied by emptying the jar and putting
    // back what survived.
    if (cleared) {
      await this.context.clearCookies();
      for (const [cookieName, cookieValue] of this.cookies) {
        add.push({
          name: cookieName,
          value: cookieValue,
          domain: host,
          path: "/",
          secure: false,
          sameSite: "Lax",
        });
      }
    }
    if (add.length > 0) await this.context.addCookies(add);
    return response;
  }

  /**
   * The raw verbs. Tests reach for these when they are asserting an HTTP fact
   * -- a status code, a header, an error envelope -- rather than using one of
   * the named helpers below.
   */
  async get(path: string, options: RequestOptions = {}): Promise<APIResponse> {
    return this.absorb(await this.transport.get(path, options));
  }

  async post(path: string, options: RequestOptions = {}): Promise<APIResponse> {
    return this.absorb(
      await this.transport.post(path, {
        ...options,
        headers: {
          ...(options.requestedWith === false ? {} : REQUESTED_WITH),
          ...options.headers,
        },
      }),
    );
  }

  async patch(path: string, options: RequestOptions = {}): Promise<APIResponse> {
    return this.absorb(
      await this.transport.patch(path, {
        ...options,
        headers: {
          ...(options.requestedWith === false ? {} : REQUESTED_WITH),
          ...options.headers,
        },
      }),
    );
  }

  async delete(path: string, options: RequestOptions = {}): Promise<APIResponse> {
    return this.absorb(
      await this.transport.delete(path, {
        ...options,
        headers: {
          ...(options.requestedWith === false ? {} : REQUESTED_WITH),
          ...options.headers,
        },
      }),
    );
  }

  async register(credentials: Credentials): Promise<void> {
    const response = await this.post("/api/v1/auth/register", {
      data: {
        email: credentials.email,
        password: credentials.password,
        full_name: credentials.fullName,
      },
    });
    expect(response.status(), await response.text()).toBe(201);
  }

  async login(credentials: Credentials): Promise<void> {
    const response = await this.post("/api/v1/auth/login", {
      data: { email: credentials.email, password: credentials.password },
    });
    expect(response.status(), await response.text()).toBe(200);
    expect(this.cookie("orbit_access"), "login did not set a session cookie").toBeTruthy();
  }

  async createWorkspace(name: string): Promise<string> {
    const response = await this.post("/api/v1/workspaces", { data: { name } });
    expect(response.status(), await response.text()).toBe(201);
    return (await response.json()).id as string;
  }

  /**
   * Uploads one file and returns the new document's id.
   *
   * The body is the **raw bytes**, not `multipart/form-data`, and the metadata
   * travels as query parameters -- that is the contract (see the route's own
   * description), and it is what lets the server stream a large file to object
   * storage without buffering a MIME envelope.
   *
   * The document is *queued* when this returns, not ready; callers that need
   * text to search wait with {@link waitForReady}.
   */
  async upload(
    workspaceId: string,
    file: { filename: string; contentType: string; body: Buffer },
    query: Record<string, string> = {},
  ): Promise<string> {
    const response = await this.rawUpload(workspaceId, file, query);
    expect(response.status, response.body).toBe(201);
    return JSON.parse(response.body).document.id as string;
  }

  /** The same request, with the outcome returned rather than asserted. */
  async rawUpload(
    workspaceId: string,
    file: { filename: string; contentType: string; body: Buffer },
    query: Record<string, string> = {},
  ): Promise<{ status: number; body: string }> {
    const params = new URLSearchParams({ filename: file.filename, ...query });
    const response = await this.post(
      `/api/v1/workspaces/${workspaceId}/documents?${params.toString()}`,
      {
        headers: { ...REQUESTED_WITH, "Content-Type": file.contentType },
        data: file.body,
      },
    );
    return { status: response.status(), body: await response.text() };
  }

  async document(workspaceId: string, documentId: string): Promise<Record<string, unknown>> {
    const response = await this.get(`/api/v1/workspaces/${workspaceId}/documents/${documentId}`);
    expect(response.status(), await response.text()).toBe(200);
    return await response.json();
  }

  /**
   * Polls until the document's current version reports `ready`, and fails loudly
   * with the recorded failure if it reports `failed`.
   *
   * The generous default covers cold worker startup on the first upload of a run
   * (see the note on the worker's web server in `playwright.config.ts`). It is a
   * ceiling, not a sleep: a document that is ready in two seconds returns in two.
   */
  async waitForReady(
    workspaceId: string,
    documentId: string,
    timeoutMs = 75_000,
  ): Promise<Record<string, unknown>> {
    const deadline = Date.now() + timeoutMs;
    let last: Record<string, unknown> = {};
    while (Date.now() < deadline) {
      last = await this.document(workspaceId, documentId);
      const version = last.current_version as Record<string, unknown> | null;
      const status = version?.status;
      if (status === "ready") return last;
      if (status === "failed") {
        throw new Error(
          `document ${documentId} failed to process: ` +
            `${version?.failure_code} ${version?.failure_reason}`,
        );
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    throw new Error(
      `document ${documentId} was not ready within ${timeoutMs}ms; last seen ` +
        JSON.stringify(last.current_version),
    );
  }

  async search(
    workspaceId: string,
    query: string,
    extra: Record<string, unknown> = {},
  ): Promise<Record<string, unknown>> {
    const response = await this.post(`/api/v1/workspaces/${workspaceId}/search`, {
      headers: REQUESTED_WITH,
      data: { query, ...extra },
    });
    expect(response.status(), await response.text()).toBe(200);
    return await response.json();
  }
}
