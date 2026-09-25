/**
 * Brings the end-to-end database to a known state before any test runs.
 *
 * Resetting here rather than between tests is deliberate. Each spec creates its
 * own user and workspace, so tests are already isolated from one another by the
 * tenancy boundary the product enforces -- the same boundary `security.spec.ts`
 * asserts. Truncating between tests would add minutes and prove nothing extra.
 */

import { execFileSync } from "node:child_process";

import { adminDatabaseUrl, backendEnv, databaseUrl, e2eDatabase, redisUrls, repoRoot } from "./env";

function run(command: string, args: string[], env: Record<string, string>): void {
  execFileSync(command, args, {
    cwd: `${repoRoot}/backend`,
    env: { ...process.env, ...env },
    stdio: "inherit",
    // `uv` is a `.cmd` shim on Windows, which execFile cannot start directly.
    shell: process.platform === "win32",
  });
}

export default function globalSetup(): void {
  process.stdout.write(`\ne2e: provisioning ${e2eDatabase}\n`);

  run("uv", ["run", "python", "../e2e/provision_db.py"], {
    E2E_ADMIN_DATABASE_URL: adminDatabaseUrl,
    E2E_DATABASE_URL: databaseUrl,
    // Flushed alongside the schema: rate-limit counters outlive a run by an
    // hour, and a run that inherits them fails for reasons that are not the
    // code's. Only the databases this environment owns.
    E2E_REDIS_URLS: redisUrls.join(","),
  });

  process.stdout.write("e2e: running migrations\n");
  run("uv", ["run", "alembic", "upgrade", "head"], backendEnv);
}
