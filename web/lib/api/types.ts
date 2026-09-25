/**
 * Types derived from the generated OpenAPI schema (`generated.ts`).
 *
 * Nothing here is hand-written to mirror a backend model. Request and response
 * shapes are *computed* from `paths`, and the client is generic over the path
 * literal, so a route that is renamed or removed in the backend stops compiling
 * here instead of failing at runtime.
 */
import type { components, paths } from "@/lib/api/generated";

export type Schemas = components["schemas"];
export type Schema<K extends keyof Schemas> = Schemas[K];

export type HttpMethod = "get" | "post" | "patch" | "put" | "delete";

/** Paths that define an operation for method `M`. */
export type PathsWith<M extends HttpMethod> = {
  [P in keyof paths]: paths[P] extends { readonly [K in M]: infer O }
    ? [O] extends [never]
      ? never
      : P
    : never;
}[keyof paths];

type Operation<P extends keyof paths, M extends HttpMethod> = paths[P] extends {
  readonly [K in M]: infer O;
}
  ? O
  : never;

type JsonContent<R> = R extends { content: { "application/json": infer B } } ? B : never;

type SuccessStatus = 200 | 201 | 202;

/** The JSON body of an operation's success response; `undefined` for a 204. */
export type ResponseBody<P extends keyof paths, M extends HttpMethod> =
  Operation<P, M> extends { responses: infer R }
    ? [JsonContent<R[Extract<keyof R, SuccessStatus>]>] extends [never]
      ? undefined
      : JsonContent<R[Extract<keyof R, SuccessStatus>]>
    : never;

export type PathParamsOf<P extends keyof paths, M extends HttpMethod> = PathParams<Operation<P, M>>;

type PathParams<O> = O extends { parameters: { path: infer X } } ? X : never;
type QueryParams<O> = O extends { parameters: { query?: infer X } }
  ? Exclude<X, undefined | never>
  : never;
type JsonBody<O> = O extends { requestBody: { content: { "application/json": infer B } } }
  ? B
  : never;

type Optional<T> = [T] extends [never] ? unknown : T;

/**
 * What a call may carry, derived from the operation: `path` is required exactly
 * when the route has path parameters, `json` exactly when it has a JSON body.
 */
export type RequestOptions<P extends keyof paths, M extends HttpMethod> = {
  signal?: AbortSignal;
  headers?: HeadersInit;
  /** Set `false` on calls that establish or end a session; see `TransportOptions`. */
  refresh?: boolean;
} & ([PathParams<Operation<P, M>>] extends [never]
  ? unknown
  : { path: Optional<PathParams<Operation<P, M>>> }) &
  ([QueryParams<Operation<P, M>>] extends [never]
    ? unknown
    : { query?: Optional<QueryParams<Operation<P, M>>> }) &
  ([JsonBody<Operation<P, M>>] extends [never] ? unknown : { json: JsonBody<Operation<P, M>> });

/** Whether any property of `T` is required, so the options argument can be. */
type HasRequired<T> = object extends T ? false : true;

export type CallArguments<P extends keyof paths, M extends HttpMethod> =
  HasRequired<RequestOptions<P, M>> extends true
    ? [options: RequestOptions<P, M>]
    : [options?: RequestOptions<P, M>];

/** Options accepted by the untyped transport, after path templating. */
export interface TransportOptions {
  method: HttpMethod;
  path?: Record<string, string | number> | undefined;
  query?: object | undefined;
  json?: unknown;
  /** A raw request body (uploads). Mutually exclusive with `json`. */
  body?: BodyInit | undefined;
  signal?: AbortSignal | undefined;
  headers?: HeadersInit | undefined;
  /**
   * Endpoints that establish or end a session must not attempt a transparent
   * refresh: a failed login is not an expired session.
   */
  refresh?: boolean;
}
