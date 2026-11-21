# Thistlewarden 0.3.2 release notes

## [0.3.2] - 2026-11-21

Beta patch release.

### Highlights

- Replace misleading **Retry failed jobs** action with **Rerun models with errors**.
- Add a mandatory review and confirmation step before creating that rerun.
- Show affected models, active testcase count, evaluated-job count, and expected target and Judge provider call counts.
- Warn that current active configuration is used, previously successful cases rerun, and new provider calls may incur cost.
- Reject stale reviews when relevant current golden-set, model, endpoint, or Judge configuration changes before confirmation.
- Add API preview support and OpenAPI documentation for reviewed reruns.

### Compatibility

No database migration is added. Existing databases, run snapshots, golden-set interchange files, and API clients remain readable. Clients using `POST /api/v1/runs/{run_id}/rerun-failures` must first request its preview and provide returned `fingerprint`.

### Upgrade notes

Before upgrade:

1. Stop existing application process.
2. Create and verify database backup.
3. Install or build version 0.3.2.
4. Start exactly one application process.
5. For a run with errors, open **Rerun models with errors**, review scope and provider-call counts, then confirm creation.

To roll back, stop 0.3.2, select pre-upgrade backup as new `DATABASE_PATH`, and run version 0.3.1. No down migrations are provided. Retain failed-upgrade files for diagnosis.

### Supported boundary

Thistlewarden 0.3.2 supports one trusted user on one host. Keep application bound to loopback. It does not provide user authentication, tenant isolation, or supported LAN/internet deployment. Provider calls remain server-side. SQLite, temporary exports, and backups require local filesystem.

### Known limitations

- Synchronous run exports support at most 50,000 jobs per run; split larger evaluations before export.
- Provider destination checks do not pin resolved IP into HTTP transport ([#22](https://github.com/lmeulen/Thistlewarden/issues/22)).
- Deliberation and sequential per-metric Judge modes can increase output tokens, latency, and cost.
- Models unable to reliably emit exact final-assessment boundary should use strict JSON-only mode.

[0.3.2]: https://github.com/lmeulen/Thistlewarden/releases/tag/v0.3.2
[0.3.1]: https://github.com/lmeulen/Thistlewarden/releases/tag/v0.3.1
