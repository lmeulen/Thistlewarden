# Thistlewarden 0.2.2 release notes

## [0.2.2] - 2026-11-18

Beta patch release.

### Highlights

- Stream JSON, CSV, and HTML run exports through restrictive temporary files and bounded database batches instead of materializing complete exports in process memory.
- Limit synchronous exports to 50,000 run jobs, checked before temporary-file creation, with guidance to split larger evaluations into smaller runs.
- Remove temporary export artifacts after web response closure, command-line transfer, or generation failure.
- Make `docs/manual/` the sole authored user-manual source and package one byte-identical manual tree in wheel and source distributions.
- Resolve manual content deterministically from source checkouts and installed distribution metadata, independent of current working directory.
- Verify manual content in wheel, source distribution, isolated installation, CI, and container release checks.
- Refresh all user-manual screenshots from current GUI using one documented, deterministic synthetic fixture and consistent capture settings.

### Compatibility

JSON and CSV exports retain deterministic ordering and existing machine-readable content. HTML remains a human-readable summary and result table, but no longer embeds a second complete JSON representation. Use JSON export for complete machine-readable snapshots, jobs, attempts, results, and quality gates.

Manual URLs and application navigation remain unchanged. Packaging no longer includes duplicate content under `evaluator/manual`; installed manual files live under `share/thistlewarden/manual`.

### Upgrade notes

Version 0.2.2 adds no database migration. Normal startup and rollback procedures remain unchanged.

Before upgrade:

1. Stop the existing application process.
2. Create and verify a database backup.
3. Install or build version 0.2.2.
4. Start exactly one application process.
5. Verify the dashboard, user manual, and an export from an existing completed run.

To roll back, stop 0.2.2, select the pre-upgrade backup as a new `DATABASE_PATH`, and run version 0.2.1. Retain failed-upgrade files for diagnosis.

### Supported boundary

Thistlewarden 0.2.2 supports one trusted user on one host. Keep the application bound to loopback. It does not provide user authentication, tenant isolation, or supported LAN/internet deployment. Provider calls remain server-side. SQLite, temporary exports, and backups require a local filesystem.

### Known limitations

- Synchronous run exports support at most 50,000 jobs per run; split larger evaluations before export.
- Export SQL identifiers do not yet use an explicit allowlist ([#21](https://github.com/lmeulen/Thistlewarden/issues/21)).
- Provider destination checks do not pin the resolved IP into the HTTP transport ([#22](https://github.com/lmeulen/Thistlewarden/issues/22)).
- Deliberation and sequential per-metric Judge modes can increase output tokens, latency, and cost.
- Models that cannot reliably emit the exact final-assessment boundary should use strict JSON-only mode.

[0.2.2]: https://github.com/lmeulen/Thistlewarden/releases/tag/v0.2.2
[0.2.1]: https://github.com/lmeulen/Thistlewarden/releases/tag/v0.2.1
