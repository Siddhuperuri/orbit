# 0008 — npm scripts as the single cross-platform task runner

- **Status:** Accepted
- **Date:** 2026-09-09

## Context

ORBIT spans a Python backend and a TypeScript frontend, and is developed on
Windows while CI and production run on Linux. Developers and CI need one place
that names every operation — format, lint, typecheck, architecture contracts,
test, migrate, run infrastructure — so that CI and a developer's machine cannot
drift apart in what "verify" means.

`make` is the reflexive answer and is **not installed on the primary development
machine**, which is Windows. Requiring it would mean installing GNU Make plus a
POSIX shell before the repository can be used at all, and Makefile recipes that
work under `sh` frequently do not under `cmd.exe`.

## Decision

**Root `package.json` scripts** are the single task entrypoint. `npm run verify`
runs the complete gate.

The decisive property is that this adds **no dependency at all**. Node and npm
are already hard requirements — the frontend cannot be built without them — so
the task runner costs nothing beyond a file that had to exist anyway. npm
resolves scripts identically on Windows, macOS, and Linux, and CI invokes the
same script names a developer does.

Python tooling is invoked through `uv run`, which resolves the project
environment without requiring an activated virtualenv. There is no
"did you activate the venv?" failure mode.

## Alternatives considered

**Make.** Rejected: not present on the primary development platform, and recipe
portability across `sh` and `cmd.exe` is poor.

**just.** A better Make. Rejected: a genuine new tool that every developer and
every CI image must install first, to replace a file that already exists.

**Taskfile (go-task).** Same objection as `just`, plus a YAML dialect to learn.

**poethepoet.** Well-built and configured from `pyproject.toml`. Rejected: it
covers only the Python half, so the repository would still need a second runner
for the frontend — two task runners is strictly worse than one.

**Documented raw commands in the README.** Rejected: prose drifts from CI
silently, which is exactly the failure this decision exists to prevent.

## Consequences

- Script bodies must stay shell-portable. Anything beyond a simple chain of
  commands belongs in a checked-in Python or Node script that the npm script
  invokes, not inlined with shell-specific syntax.
- The root `package.json` is a task manifest, not an application package. It is
  marked `"private": true`, declares no dependencies, and is never published.
  Frontend dependencies live in `web/package.json`.
- CI calls the same `npm run` targets developers call. A gate that passes
  locally and fails in CI indicates an environment difference, not a different
  command — which is the point.
- Windows developers need no additional tooling beyond Node, Python, `uv`, and
  Docker.
