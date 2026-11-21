# Thistlewarden 0.3.1 release notes

## [0.3.1] - 2026-11-21

Beta patch release.

### Highlights

- Fix **Add user turn** in the multi-turn test-case editor.
- Document multi-turn golden-set interchange in the user manual, including JSON schema version 2.0, CSV `user_prompts` JSON arrays, and legacy single-turn import compatibility.

### Compatibility

No database migration is added. Existing single-turn and multi-turn test cases, run snapshots, and golden-set interchange files remain readable.

Golden-set JSON exports continue to use schema version 2.0 and ordered `user_prompts`. CSV exports continue to store `user_prompts` as a JSON array. Legacy JSON without a schema version and legacy CSV files with a `prompt` column import as one-turn test cases.

### Upgrade notes

Before upgrade:

1. Stop the existing application process.
2. Create and verify a database backup.
3. Install or build version 0.3.1.
4. Start exactly one application process.
5. Verify the dashboard and add a user turn to a test case.

To roll back, stop 0.3.1, select the pre-upgrade backup as a new `DATABASE_PATH`, and run version 0.3.0. Retain failed-upgrade files for diagnosis.

### Supported boundary

Thistlewarden 0.3.1 supports one trusted user on one host. Keep the application bound to loopback. It does not provide user authentication, tenant isolation, or supported LAN/internet deployment. Provider calls remain server-side. SQLite, temporary exports, and backups require a local filesystem.

### Known limitations

- Synchronous run exports support at most 50,000 jobs per run; split larger evaluations before export.
- Provider destination checks do not pin the resolved IP into the HTTP transport ([#22](https://github.com/lmeulen/Thistlewarden/issues/22)).
- Deliberation and sequential per-metric Judge modes can increase output tokens, latency, and cost.
- Models that cannot reliably emit the exact final-assessment boundary should use strict JSON-only mode.

[0.3.1]: https://github.com/lmeulen/Thistlewarden/releases/tag/v0.3.1
[0.3.0]: https://github.com/lmeulen/Thistlewarden/releases/tag/v0.3.0
