#!/usr/bin/env node
/**
 * Generates the frontend's view of the backend contract (ADR-0016): the frontend
 * never hand-writes an interface that mirrors a Pydantic model, so a backend
 * contract change becomes a compile error instead of a runtime surprise.
 *
 *   lib/api/generated.ts   types from the FastAPI application's OpenAPI schema
 *   lib/api/access.ts      the role -> permission map, from `orbit.domain.access`
 *                          (used to avoid *offering* actions a role cannot
 *                          perform; the API still enforces every one of them)
 *
 *   npm run api:types          regenerate the committed files
 *   npm run api:types:check    fail if a committed file is out of date (CI)
 *
 * Both are read by importing the backend package rather than calling a running
 * server, so this needs no database, Redis, or object store. `create_app()` builds
 * routes and middleware only; connections are opened in the lifespan, which never
 * runs here.
 */
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import openapiTS, { astToString } from "openapi-typescript";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const backendRoot = path.resolve(webRoot, "..", "backend");
const schemaOutput = path.join(webRoot, "lib", "api", "generated.ts");
const accessOutput = path.join(webRoot, "lib", "api", "access.ts");
const checkOnly = process.argv.includes("--check");

// `Settings` requires these to construct, but nothing here connects to them.
// Pinned values override the environment because the schema must not depend on
// a developer's local `.env`; the rest are supplied only when absent, so a real
// environment (or CI's) is never overridden by a placeholder.
const pinnedEnvironment = {
  ORBIT_ENV: "development",
  ORBIT_AI_PROVIDER: "fake",
  ORBIT_EMAIL_PROVIDER: "console",
};
const defaultEnvironment = {
  ORBIT_DATABASE_URL: "postgresql+asyncpg://schema:schema@127.0.0.1:5432/schema",
  ORBIT_REDIS_URL: "redis://127.0.0.1:6379/0",
  ORBIT_CELERY_BROKER_URL: "redis://127.0.0.1:6379/1",
  ORBIT_CELERY_RESULT_BACKEND: "redis://127.0.0.1:6379/2",
  ORBIT_S3_BUCKET: "schema",
  ORBIT_EMBEDDING_DIMENSIONS: "1536",
  ORBIT_S3_ACCESS_KEY_ID: "schema",
  ORBIT_S3_SECRET_ACCESS_KEY: "schema-schema-schema",
  ORBIT_SECRET_KEY: "schema-export-only-not-a-real-secret-0123456789",
};

const PYTHON_EXPORT = [
  "import json, sys",
  "from orbit.composition.app import create_app",
  "from orbit.domain.access import ROLE_PERMISSIONS",
  "with open(sys.argv[1], 'w', encoding='utf-8') as handle:",
  "    json.dump(create_app().openapi(), handle, sort_keys=True)",
  "roles = {role.value: sorted(p.value for p in perms) for role, perms in ROLE_PERMISSIONS.items()}",
  "with open(sys.argv[2], 'w', encoding='utf-8') as handle:",
  "    json.dump(roles, handle, sort_keys=True)",
].join("\n");

function exportContract(schemaPath, accessPath) {
  const environment = { ...defaultEnvironment, ...process.env, ...pinnedEnvironment };

  const result = spawnSync("uv", ["run", "python", "-c", PYTHON_EXPORT, schemaPath, accessPath], {
    cwd: backendRoot,
    env: environment,
    encoding: "utf-8",
    // `uv` is an executable on every platform and resolves without a shell.
    shell: false,
  });

  if (result.error) {
    throw new Error(`Could not run \`uv\` to export the backend contract: ${result.error.message}`);
  }
  if (result.status !== 0) {
    throw new Error(`Exporting the backend contract failed:\n${result.stderr || result.stdout}`);
  }
}

const SCHEMA_HEADER = [
  "/**",
  " * GENERATED FILE -- DO NOT EDIT.",
  " *",
  " * Produced by `npm run api:types` from the backend's OpenAPI schema. Regenerate",
  " * it whenever an API route or schema changes; CI fails when it is stale.",
  " */",
  "",
  "",
].join("\n");

function accessModule(roles) {
  const roleNames = Object.keys(roles).sort();
  const permissions = [...new Set(Object.values(roles).flat())].sort();
  const list = (values, indent) => values.map((value) => `${indent}"${value}",`).join("\n");
  const table = roleNames
    .map((role) => `  ${role}: [\n${list(roles[role], "    ")}\n  ],`)
    .join("\n");

  return [
    "/**",
    " * GENERATED FILE -- DO NOT EDIT.",
    " *",
    " * The backend's role -> permission map (`orbit.domain.access.ROLE_PERMISSIONS`),",
    " * exported by `npm run api:types`. The UI uses it only to avoid *offering* actions",
    " * a role cannot perform; the API enforces every permission regardless.",
    " */",
    "",
    "export const PERMISSIONS = [",
    list(permissions, "  "),
    "] as const;",
    "",
    "export type Permission = (typeof PERMISSIONS)[number];",
    "",
    "export const ROLES = [",
    list(roleNames, "  "),
    "] as const;",
    "",
    "export type RoleName = (typeof ROLES)[number];",
    "",
    "export const ROLE_PERMISSIONS: Readonly<Record<RoleName, readonly Permission[]>> = {",
    table,
    "};",
    "",
  ].join("\n");
}

const normalise = (text) => text.replace(/\r\n/g, "\n");
const readIfPresent = (file) => (existsSync(file) ? normalise(readFileSync(file, "utf-8")) : "");

const workDirectory = mkdtempSync(path.join(tmpdir(), "orbit-contract-"));
try {
  const schemaPath = path.join(workDirectory, "openapi.json");
  const accessPath = path.join(workDirectory, "access.json");
  exportContract(schemaPath, accessPath);

  const schema = JSON.parse(readFileSync(schemaPath, "utf-8"));
  const ast = await openapiTS(schema, { alphabetize: true, immutable: true });

  const outputs = [
    [schemaOutput, SCHEMA_HEADER + normalise(astToString(ast))],
    [accessOutput, accessModule(JSON.parse(readFileSync(accessPath, "utf-8")))],
  ];

  if (checkOnly) {
    const stale = outputs.filter(([file, content]) => readIfPresent(file) !== content);
    if (stale.length > 0) {
      const names = stale.map(([file]) => path.relative(webRoot, file)).join(", ");
      console.error(
        `Out of date with the backend contract: ${names}\n` +
          "Run `npm run web:api-types` (from the repository root) and commit the result.",
      );
      process.exit(1);
    }
    process.stdout.write("Generated API contract files are up to date.\n");
  } else {
    for (const [file, content] of outputs) {
      writeFileSync(file, content, "utf-8");
      process.stdout.write(`Wrote ${path.relative(webRoot, file)}\n`);
    }
  }
} finally {
  rmSync(workDirectory, { recursive: true, force: true });
}
