/**
 * The environment the end-to-end stack runs in.
 *
 * E2E gets its *own* database, S3 prefix, Redis databases, and ports, so that a
 * run never touches the developer's working stack: `npm run dev:api` can stay
 * up on 8000 while the suite drives its own API on 8100. Sharing them would
 * make the suite destructive -- `global-setup` drops and re-migrates the schema
 * it is given -- and that is not a thing a test command may do to a database
 * someone is using.
 *
 * Credentials come from the repository's `.env` (the same file compose reads);
 * nothing is hardcoded here, and nothing is read from a real deployment.
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
export const repoRoot = resolve(here, "..");

/**
 * Minimal `.env` reader. dotenv is not a dependency of this package: the file's
 * grammar here is `KEY=VALUE` with `#` comments, which is all compose writes,
 * and a parser for that is shorter than the dependency's lockfile entry.
 *
 * Values already present in the real environment win, so CI can supply secrets
 * without a file on disk.
 */
function loadDotEnv(path: string): Record<string, string> {
  if (!existsSync(path)) return {};
  const out: Record<string, string> = {};
  for (const line of readFileSync(path, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    let value = trimmed.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    out[key] = value;
  }
  return out;
}

const dotEnv = loadDotEnv(resolve(repoRoot, ".env"));

function env(key: string, fallback?: string): string {
  const value = process.env[key] ?? dotEnv[key] ?? fallback;
  if (value === undefined) {
    throw new Error(
      `${key} is not set. The end-to-end suite reads it from the environment or from ` +
        `${resolve(repoRoot, ".env")}; copy .env.example and fill it in.`,
    );
  }
  return value;
}

/**
 * `127.0.0.1`, never `localhost`. On this project's Windows/WSL setup `localhost`
 * resolves to `::1` first and a WSL-forwarded port listens only on IPv4, so a
 * connection to `localhost` hangs until it times out rather than failing fast.
 */
const HOST = "127.0.0.1";

export const ports = {
  api: Number(process.env.E2E_API_PORT ?? 8100),
  web: Number(process.env.E2E_WEB_PORT ?? 3100),
  /**
   * The worker's Prometheus port. It is bound from the `worker_ready` signal,
   * which makes it the one honest readiness gate a Celery worker has: it starts
   * answering exactly when the worker has registered and can take a job.
   */
  workerMetrics: Number(process.env.E2E_WORKER_METRICS_PORT ?? 9110),
} as const;

const pgUser = env("POSTGRES_USER", "orbit");
const pgPassword = env("POSTGRES_PASSWORD");
const pgPort = env("POSTGRES_PORT", "5432");

/** A database of its own. `global-setup` drops and re-migrates this, and only this. */
export const e2eDatabase = process.env.E2E_DATABASE_NAME ?? "orbit_e2e";

export const databaseUrl = `postgresql+asyncpg://${pgUser}:${encodeURIComponent(
  pgPassword,
)}@${HOST}:${pgPort}/${e2eDatabase}`;

/** The maintenance connection used only to `CREATE DATABASE` the one above. */
export const adminDatabaseUrl = `postgresql+asyncpg://${pgUser}:${encodeURIComponent(
  pgPassword,
)}@${HOST}:${pgPort}/${env("POSTGRES_DB", "orbit")}`;

/**
 * Redis databases 12-14: cache, Celery broker, Celery results. The unit suite
 * owns 9-11 and a real deployment owns 0-2, so nothing here can flush either.
 * `global-setup` empties exactly these three before a run.
 */
export const redisUrls = [12, 13, 14].map((db) => `redis://${HOST}:6379/${db}`) as [
  string,
  string,
  string,
];

export const webBaseUrl = process.env.E2E_WEB_URL ?? `http://${HOST}:${ports.web}`;
export const apiBaseUrl = process.env.E2E_API_URL ?? `http://${HOST}:${ports.api}`;
export const workerMetricsUrl = `http://${HOST}:${ports.workerMetrics}/metrics`;

/**
 * Environment for the API and worker processes.
 *
 * `ORBIT_AI_PROVIDER=fake` is the point of the whole file: the fake embedding
 * and answer providers are deterministic and offline (ADR-0007), so the suite
 * asserts exact retrieval and citation behaviour and **no run can depend on a
 * real API key**. A key present in the developer's `.env` is deliberately not
 * forwarded.
 */
export const backendEnv: Record<string, string> = {
  ORBIT_ENV: "development",
  ORBIT_LOG_FORMAT: "console",
  ORBIT_LOG_LEVEL: "WARNING",
  ORBIT_DATABASE_URL: databaseUrl,
  ORBIT_REDIS_URL: redisUrls[0],
  ORBIT_CELERY_BROKER_URL: redisUrls[1],
  ORBIT_CELERY_RESULT_BACKEND: redisUrls[2],
  ORBIT_S3_ENDPOINT_URL: `http://${HOST}:9000`,
  ORBIT_S3_BUCKET: env("ORBIT_S3_BUCKET", "orbit-documents"),
  ORBIT_S3_ACCESS_KEY_ID: env("MINIO_ROOT_USER"),
  ORBIT_S3_SECRET_ACCESS_KEY: env("MINIO_ROOT_PASSWORD"),
  ORBIT_SECRET_KEY: env("ORBIT_SECRET_KEY"),
  ORBIT_AI_PROVIDER: "fake",
  /**
   * Required, not optional, for the same reason `npm run dev` needs it.
   *
   * ORBIT is one origin to the browser, but Next's rewrite cannot preserve
   * `Host`: the browser sends `Origin: …:3100` while the request arrives at the
   * API as `Host: …:8100`. The CSRF middleware compares the two, so without
   * this every cookie-authenticated mutation -- including signing out -- is a
   * 403 (ADR-0009). Naming the web origin here is what a reverse proxy does in
   * production by preserving `Host`.
   */
  ORBIT_CORS_ALLOWED_ORIGINS: `http://${HOST}:${ports.web}`,
  ORBIT_EMBEDDING_DIMENSIONS: env("ORBIT_EMBEDDING_DIMENSIONS", "1536"),
  // Writes reset and verification links to the log instead of sending mail, so
  // the suite can exercise those flows. It is refused in production.
  ORBIT_EMAIL_PROVIDER: "console",

  // ---------------------------------------------------------------------
  // Per-IP rate limits, raised.
  //
  // Every request in the suite arrives from 127.0.0.1, so to the limiter the
  // whole run is one extremely busy address -- registration alone ships with
  // 10 per IP per hour, which the suite exhausts before its third file. These
  // are raised so the limiter stops being a global failure of the run.
  //
  // The **per-account** limits are deliberately left at their production
  // defaults. They are what `security.spec.ts` uses to prove brute-force
  // throttling actually works: its 25 wrong guesses at one account trip the
  // per-account limit of 10 long before any of these ceilings. Raising those
  // too would turn that test into one that can never fail.
  // ---------------------------------------------------------------------
  ORBIT_ACCOUNT_EMAIL_RATE_LIMIT_PER_IP: "1000",
  ORBIT_LOGIN_RATE_LIMIT_PER_IP: "10000",
  ORBIT_UPLOAD_RATE_LIMIT_PER_IP: "10000",
  ORBIT_SEARCH_RATE_LIMIT_PER_IP: "100000",
  ORBIT_CHAT_RATE_LIMIT_PER_IP: "100000",
};

/** The API serves no metrics port here: nothing polls it, and 9100 may be a dev instance's. */
export const apiEnv: Record<string, string> = {
  ...backendEnv,
  ORBIT_SERVICE_NAME: "orbit-api",
  ORBIT_METRICS_ENABLED: "false",
};

/**
 * The worker *does* serve one, on a port of its own, because that endpoint is
 * how Playwright knows the worker is ready rather than merely spawned.
 */
export const workerEnv: Record<string, string> = {
  ...backendEnv,
  ORBIT_SERVICE_NAME: "orbit-worker",
  ORBIT_METRICS_ENABLED: "true",
  ORBIT_METRICS_HOST: HOST,
  ORBIT_WORKER_METRICS_PORT: String(ports.workerMetrics),
};
