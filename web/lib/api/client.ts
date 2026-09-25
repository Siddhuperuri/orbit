import { isAbortError, networkError, toApiError } from "@/lib/api/errors";
import { withSessionRefresh } from "@/lib/api/session";
import type {
  CallArguments,
  HttpMethod,
  PathsWith,
  ResponseBody,
  TransportOptions,
} from "@/lib/api/types";

/**
 * The HTTP client. Every request to the ORBIT API goes through here.
 *
 * Paths are the OpenAPI path templates themselves (`/api/v1/workspaces/{workspace_id}`),
 * and the response, path parameters, query, and JSON body are all inferred from
 * the generated schema -- so the compiler, not a code review, notices when a
 * route changes.
 *
 * The URL is a *relative path*, never an origin. ORBIT is served from a single
 * origin (ADR-0009): the browser talks only to the host that served the page, and
 * `/api/*` is routed to the backend by a rewrite in development and a reverse
 * proxy in production. Hardcoding an absolute origin would break the HttpOnly
 * auth cookies, so it is a bug, not a configuration choice.
 */

/**
 * Sent on mutating requests as defence in depth behind `SameSite=Lax`. A
 * cross-site attacker cannot set a custom header without the server's CORS
 * consent, so its presence is itself evidence the request came from our origin.
 */
const REQUESTED_WITH = { name: "X-Requested-With", value: "orbit-web" } as const;

/** Substitutes `{name}` placeholders, percent-encoding every value. */
export function fillPath(template: string, params: Record<string, string | number> = {}): string {
  return template.replace(/\{([^}]+)\}/g, (_match, name: string) => {
    const value = params[name];
    if (value === undefined) {
      throw new Error(`Missing path parameter "${name}" for ${template}`);
    }
    return encodeURIComponent(String(value));
  });
}

/** Serialises a query object; `undefined`/`null` are omitted, arrays repeat the key. */
export function buildQuery(query: object | undefined): string {
  if (!query) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      for (const item of value) search.append(key, String(item));
    } else {
      search.set(key, String(value));
    }
  }
  const serialised = search.toString();
  return serialised ? `?${serialised}` : "";
}

export function buildUrl(
  template: string,
  path?: TransportOptions["path"],
  query?: TransportOptions["query"],
): string {
  return `${fillPath(template, path)}${buildQuery(query)}`;
}

/** One attempt: a single `fetch`, with network failures mapped to {@link ApiError}. */
async function sendOnce(template: string, options: TransportOptions): Promise<Response> {
  const headers = new Headers(options.headers);
  // A streaming call asks for `text/event-stream` itself; everything else wants JSON.
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  if (options.method !== "get") headers.set(REQUESTED_WITH.name, REQUESTED_WITH.value);

  let body: BodyInit | undefined;
  if (options.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.json);
  } else if (options.body !== undefined) {
    body = options.body;
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(template, options.path, options.query), {
      method: options.method.toUpperCase(),
      headers,
      body,
      signal: options.signal,
      // Auth travels as HttpOnly cookies, so credentials must be included even
      // same-origin for the request to carry a session.
      credentials: "same-origin",
    });
  } catch (cause) {
    // fetch rejects only when the request never completed -- offline, DNS
    // failure, or an aborted connection. An HTTP error status resolves.
    if (isAbortError(cause)) throw cause;
    throw networkError(cause);
  }

  if (!response.ok) throw await toApiError(response);
  return response;
}

/**
 * Send a request and return the raw, already-checked `Response`. Streaming
 * consumers read its body themselves; everything else uses {@link api}.
 */
export function sendRequest(template: string, options: TransportOptions): Promise<Response> {
  const attempt = () => sendOnce(template, options);
  return options.refresh === false ? attempt() : withSessionRefresh(attempt);
}

async function parseBody(response: Response): Promise<unknown> {
  // Some success responses have no body (204, and 202 for the password-reset
  // request). Reading text first handles all of them without a per-status branch.
  const text = await response.text();
  return text === "" ? undefined : (JSON.parse(text) as unknown);
}

async function call(template: string, options: TransportOptions): Promise<unknown> {
  return parseBody(await sendRequest(template, options));
}

type PublicOptions = Partial<
  Pick<TransportOptions, "path" | "query" | "json" | "signal" | "headers" | "refresh">
>;

function method<M extends HttpMethod>(verb: M) {
  return <P extends PathsWith<M>>(
    path: P,
    ...args: CallArguments<P, M>
  ): Promise<ResponseBody<P, M>> => {
    const options = (args[0] ?? {}) as PublicOptions;
    return call(path, { ...options, method: verb }) as Promise<ResponseBody<P, M>>;
  };
}

export const api = {
  get: method("get"),
  post: method("post"),
  patch: method("patch"),
  put: method("put"),
  delete: method("delete"),
};

/**
 * Auth-establishing calls opt out of the transparent refresh: a rejected login
 * is a wrong password, not an expired session, and must never trigger a refresh.
 */
export const NO_REFRESH = { refresh: false } as const;
