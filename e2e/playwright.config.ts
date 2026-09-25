import { defineConfig, devices } from "@playwright/test";

import {
  apiBaseUrl,
  apiEnv,
  ports,
  repoRoot,
  webBaseUrl,
  workerEnv,
  workerMetricsUrl,
} from "./env";

/**
 * End-to-end tests drive a **real stack**: a production build of the Next app, the
 * real FastAPI service, and a real Celery worker, against the PostgreSQL, Redis, and
 * MinIO from `npm run infra:up`. Nothing between the browser and the database is a
 * double. The only substituted component is the AI provider, which is ORBIT's own
 * `fake` provider (ADR-0007) -- deterministic, offline, and a supported operating
 * mode rather than a test stub, which is what lets these tests assert exact
 * retrieval and citation results and never need a credential.
 *
 * The three services run on ports of their own (8100/3100) against a database of
 * their own, so a developer's `npm run dev:api` and `npm run dev:web` can stay up.
 */
export default defineConfig({
  testDir: "./tests",
  globalSetup: "./global-setup.ts",
  outputDir: "./.artifacts/test-results",

  /**
   * Serial. The worker runs `--pool=solo` (prefork does not work on Windows) so it
   * processes one document at a time; parallel uploads would queue behind each other
   * and turn the processing wait into a flake. Correctness here is worth the minutes.
   */
  workers: 1,
  fullyParallel: false,

  // A test that only passes on the second attempt is a test that is lying. CI gets
  // one retry for genuine infrastructure flakiness and the report shows it happened.
  retries: process.env.CI ? 1 : 0,
  forbidOnly: !!process.env.CI,

  timeout: 90_000,
  expect: { timeout: 15_000 },

  reporter: process.env.CI
    ? [["list"], ["html", { outputFolder: ".artifacts/html", open: "never" }], ["github"]]
    : [["list"], ["html", { outputFolder: ".artifacts/html", open: "never" }]],

  use: {
    baseURL: webBaseUrl,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    // Deterministic rendering: a viewport that changes between machines changes
    // which elements are in view and which responsive layout renders.
    viewport: { width: 1280, height: 900 },
    timezoneId: "UTC",
    locale: "en-US",
  },

  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],

  webServer: [
    {
      name: "api",
      command:
        "uv run uvicorn orbit.composition.asgi:app " +
        `--host 127.0.0.1 --port ${ports.api} --log-level warning`,
      cwd: `${repoRoot}/backend`,
      env: apiEnv,
      // `/healthz` answers before any dependency is reachable, which is the point
      // of a liveness probe. Readiness (`/readyz`) is asserted by the suite itself,
      // in `reliability.spec.ts`, where a 503 is a result rather than a startup
      // failure -- polling it here would make Playwright treat "degraded" as "dead".
      url: `${apiBaseUrl}/healthz`,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      name: "worker",
      // `--pool=solo` because prefork requires fork(2) and this suite must run on
      // Windows as well as CI. The trade-off is the serial execution above.
      command:
        "uv run celery --app orbit.composition.worker:celery_app worker " +
        "--pool=solo --loglevel=WARNING",
      cwd: `${repoRoot}/backend`,
      env: workerEnv,
      // The worker's own Prometheus port, bound from the `worker_ready` signal --
      // so this gate means "the worker has registered and can take a job", not
      // merely "the process was spawned". Nothing here starts until that is true,
      // which is what keeps the first upload's wait from absorbing a cold start.
      url: workerMetricsUrl,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      name: "web",
      command: `npm run build && npm run start -- --port ${ports.web}`,
      cwd: `${repoRoot}/web`,
      env: {
        // A production build, not `next dev`: it is what a user is served, and it
        // removes HMR and on-demand compilation as sources of timing flake.
        NODE_ENV: "production",
        ORBIT_DIST_DIR: ".next-e2e",
        ORBIT_DEV_API_PROXY_TARGET: apiBaseUrl,
      },
      url: webBaseUrl,
      reuseExistingServer: !process.env.CI,
      timeout: 300_000,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});
