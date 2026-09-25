import type { NextConfig } from "next";

/**
 * ORBIT is served from a single origin (ADR-0009): the browser must never talk
 * to a host other than the one that served the page, because both auth tokens
 * are HttpOnly cookies scoped to that origin.
 *
 * In production a reverse proxy routes `/api/*` to the backend. In development
 * this rewrite does the same job, so cookie behaviour is identical in both.
 */
const apiProxyTarget = process.env.ORBIT_DEV_API_PROXY_TARGET ?? "http://localhost:8000";

/**
 * Next's rewrite proxy ships two defaults that are wrong for this API, and both
 * fail *silently as a 500*, far from their cause:
 *
 *  - `proxyClientMaxBodySize` defaults to 10 MiB. The backend accepts uploads up to
 *    `ORBIT_MAX_UPLOAD_BYTES` (50 MiB by default, configurable to 1 GiB), so without
 *    this every upload between 10 MiB and the real limit is truncated by the proxy
 *    and the connection resets. Set to the backend's hard ceiling so the *backend's*
 *    limit -- which answers with a proper `UPLOAD_TOO_LARGE` -- is the one that
 *    applies, not an invisible one in front of it.
 *  - `proxyTimeout` defaults to 30 s of socket inactivity, which cuts a large upload
 *    while the backend streams it to storage, and an answer whose first token takes
 *    longer than that. The backend has its own timeouts for both.
 *
 * These affect only development and the compose `web` container's rewrite; in
 * production the reverse proxy in front of both services is the layer that carries
 * `/api/*`, and its own limits must be configured to match (docs/operations).
 */
const BACKEND_MAX_UPLOAD_BYTES = 1_073_741_824;
const PROXY_TIMEOUT_MS = 10 * 60_000;

/**
 * The end-to-end suite builds and serves the app on its own port while a
 * developer's `next dev` may still be running. Both would otherwise write to
 * `.next`, and two Next processes sharing one build directory corrupt each
 * other's cache. `ORBIT_DIST_DIR` lets the E2E build take a directory of its
 * own; unset -- which is every other case -- keeps Next's default.
 */
const distDir = process.env.ORBIT_DIST_DIR;

const nextConfig: NextConfig = {
  reactStrictMode: true,

  ...(distDir ? { distDir } : {}),

  experimental: {
    proxyClientMaxBodySize: BACKEND_MAX_UPLOAD_BYTES,
    proxyTimeout: PROXY_TIMEOUT_MS,
  },

  // Fail the build on a type error rather than shipping one. Next allows
  // disabling this; doing so would violate section 25 of the brief.
  //
  // Next 16 removed the build-time ESLint hook, so linting is a separate step
  // in `npm run verify` and in CI rather than a side effect of building.
  typescript: { ignoreBuildErrors: false },

  // Standalone output copies only the files the server actually needs, which
  // keeps the runtime image small and free of build tooling.
  output: "standalone",

  // The framework version is an information leak with no benefit to users.
  poweredByHeader: false,

  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiProxyTarget}/api/:path*` }];
  },
};

export default nextConfig;
