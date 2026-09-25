# Operations

- **[environment.md](environment.md)** — every configuration variable, what
  validation rejects, and how secrets are handled. Written.

- **[worker.md](worker.md)** — running the worker and scheduler, limits, retries,
  health signals, and the stuck-document runbook. Written (M4).

- **[failure-modes.md](failure-modes.md)** — what each dependency failure does,
  what keeps working, and how to tell them apart. Written.

- **[observability.md](observability.md)** — logs and correlation, health endpoints, every metric and the question it answers, suggested alerts. Written.

- **[runbook.md](runbook.md)** — investigating a failed upload, stuck job, failed embedding, slow search, slow RAG, AI provider outage, database outage. Written.

- `provisioning.md` — database roles, extensions, object storage setup (M1+)
- `backup-recovery.md` — what is backed up, restore procedure, tested RPO/RTO (M8)
- `engineering-audit.md` — the final production-readiness assessment, with evidence (M8)

Not yet written. ORBIT is not represented as production-ready until
`engineering-audit.md` exists and its findings are addressed.
