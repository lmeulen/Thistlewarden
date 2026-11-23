# Thistlewarden 0.3.3 release notes

## [0.3.3] - 2026-11-23

Beta patch release.

### Highlights

- Add selective configuration backups. Choose supported categories and individual records, with dependency closure, SHA-256 manifest integrity checks, and safe partial restore planning.
- Selectively export and restore terminal run history as immutable evidence graphs, including snapshots, jobs, provider attempts, turn checkpoints, results, and quality-gate outcomes. Active work and leases are never restored.
- Add an **Archive all current operational configuration** restore mode, which atomically archives active endpoints, model profiles, Judge profiles, and golden sets before import.
- Keep legacy complete configuration backups compatible while adding selective bundle schema version 1.1.
- Add a portable example golden set covering all six evaluation methods, including the expected answer `3` for “How many R's in strawberry?”.
- Improve run review with durable timing traces, a deterministic model chooser, model cost/quality frontier, provisional active-run quality-gate progress, and accessible collapsible result panels.
- Add bulk **Set judge** for selected LLM-judge test cases, limited to explicitly versioned published Judge definitions.
- Add an accessible page-width expanded evaluation-information dialog on result pages.
- Improve vertical spacing across page-level components and clarify the Backup and Restore layout.

### Compatibility

No database migration is added. Existing databases, configuration backups, golden-set interchange files, run snapshots, and API clients remain readable.

Complete configuration exports continue to use schema version 1.0. Selective configuration exports use schema version 1.1 and retain every supported category in the manifest. Restore rejects invalid manifests, integrity mismatches, unsafe active-run history, and duplicate immutable run IDs rather than silently changing evidence.

### Upgrade notes

Before upgrade:

1. Stop the existing application process.
2. Create and verify a database backup.
3. Install or build version 0.3.3.
4. Start exactly one application process.
5. Visit **Backup** to review selective configuration backup and restore options. Restoring with **Replace** or **Archive all current operational configuration** requires explicit confirmation.

To roll back, stop 0.3.3, select the pre-upgrade backup as the new `DATABASE_PATH`, and run version 0.3.2. No down migrations are provided. Retain failed-upgrade files for diagnosis.

### Supported boundary

Thistlewarden 0.3.3 supports one trusted user on one host. Keep the application bound to loopback. It does not provide user authentication, tenant isolation, or supported LAN/internet deployment. Provider calls remain server-side. SQLite, temporary exports, and backups require a local filesystem.

### Known limitations

- Synchronous run exports support at most 50,000 jobs per run; split larger evaluations before export.
- Provider destination checks do not pin resolved IP into HTTP transport ([#22](https://github.com/lmeulen/Thistlewarden/issues/22)).
- Deliberation and sequential per-metric Judge modes can increase output tokens, latency, and cost.
- Models unable to reliably emit an exact final-assessment boundary should use strict JSON-only mode.

[0.3.3]: https://github.com/lmeulen/Thistlewarden/releases/tag/v0.3.3
[0.3.2]: https://github.com/lmeulen/Thistlewarden/releases/tag/v0.3.2
