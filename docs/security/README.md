# Security

## Reviews

- [`auth-security-review.md`](auth-security-review.md) — **M4 authentication and
  authorization.** Nine findings and their fixes, the attack scenarios exercised,
  accepted risks with their revisit conditions, and an explicit list of what is
  **not** verified. Read §6 before treating the module as production-ready.

## Planned

- `threat-model.md` — assets, trust boundaries, attacker capabilities (M2+)
- `authentication.md` — session lifecycle; see also ADR-0003 (M2)
- `authorization.md` — the tenancy model; see also ADR-0004 (M2)
- `file-handling.md` — treating uploads as hostile input (M3)
- `disclosure.md` — how to report a vulnerability privately (M8)

## Standing rules

Secrets come from configuration, never from source. `.env` is gitignored, CI
fails if a dotenv file becomes tracked, and full branch history is scanned for
credentials on every run.
