# Thistlewarden 0.3.0 release notes

## [0.3.0] - 2026-11-21

Beta minor release.

### Highlights

- Add ordered multi-turn user prompts to test cases. Each generated assistant response becomes context for the next user turn; only the final assistant response is evaluated.
- Preserve complete ordered transcripts as run evidence. Result views and JSON, CSV, and HTML exports identify context responses separately from the evaluated final answer.
- Add durable per-turn run-job checkpoints. Interrupted target calls are not replayed automatically; recovery resumes only from completed checkpoints.
- Pass multi-turn transcript context to LLM Judges as untrusted evaluation data while retaining the final answer as the separately assessed candidate.
- Add multi-turn testcase editing controls, target-call preflight accounting, and versioned JSON/CSV golden-set interchange. Legacy single-turn test cases and interchange files remain supported.
- Terminalize pending and running jobs when cancelling a run, while preserving completed and failed historical jobs and results.
- Update active runtime and diagnostic messages to English.
- Update Mistune to 3.3.4 for CVE-2026-97698 and CVE-2026-97699.

### Compatibility

Existing single-turn test cases remain readable and execute as one-turn conversations. Legacy JSON documents without a schema version and legacy CSV files with a `prompt` column import as one-turn test cases.

New golden-set JSON exports use schema version 2.0 and canonical `user_prompts`. New run snapshots record schema version 2.0; historical snapshots remain unchanged and readable.

### Upgrade notes

Version 0.3.0 adds database migrations 21 through 23. Normal application startup makes a backup before migration, then terminalizes stale active jobs under cancelled runs, adds canonical multi-turn prompt storage, and adds durable run-job turn checkpoints.

Before upgrade:

1. Stop the existing application process.
2. Create and verify a database backup.
3. Install or build version 0.3.0.
4. Start exactly one application process and allow startup migration to finish.
5. Verify existing single-turn test cases and completed runs remain readable.
6. Create a new multi-turn test case and review its transcript after a completed run.

To roll back, stop 0.3.0, select the pre-upgrade backup as a new `DATABASE_PATH`, and run version 0.2.2. No down migrations are provided. Retain failed-upgrade files for diagnosis.

### Supported boundary

Thistlewarden 0.3.0 supports one trusted user on one host. Keep the application bound to loopback. It does not provide user authentication, tenant isolation, or supported LAN/internet deployment. Provider calls remain server-side. SQLite, temporary exports, and backups require a local filesystem.

### Known limitations

- Synchronous run exports support at most 50,000 jobs per run; split larger evaluations before export.
- Provider destination checks do not pin the resolved IP into the HTTP transport ([#22](https://github.com/lmeulen/Thistlewarden/issues/22)).
- Deliberation and sequential per-metric Judge modes can increase output tokens, latency, and cost.
- Models that cannot reliably emit the exact final-assessment boundary should use strict JSON-only mode.

[0.3.0]: https://github.com/lmeulen/Thistlewarden/releases/tag/v0.3.0
[0.2.2]: https://github.com/lmeulen/Thistlewarden/releases/tag/v0.2.2
