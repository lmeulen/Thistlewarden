# Thistlewarden 0.2.0 release notes

## [0.2.0] - 2026-11-18

Second beta release.

### Highlights

- Create local Judge definitions with configurable instructions, metrics, weights, threshold, and assessment mode.
- Publish immutable Judge versions and pin testcases and run snapshots to an exact semantic version and hash.
- Run one assessment for all selected metrics or sequential assessments per metric, with core-owned validation, weighted aggregation, and pass/fail decisions.
- Manage Judge evaluation rules and Judge execution profiles together on the **Judges** page while keeping their semantics and runtime settings separate.
- Import and export Judge drafts, immutable versions, profiles, golden sets, and testcases through versioned configuration bundles.
- Require an active Judge profile before creating runs containing LLM-Judge testcases.
- Preserve safe provider-call evidence and known cost when later per-metric assessments fail or are cancelled; incomplete evaluations never receive partial scores.
- Review and permanently delete selected testcases through the standard stale-write-protected bulk workflow.
- Show endpoint API-key presence without exposing secret values or secret identifiers.
- Add record-derived first-run onboarding and use English-only canonical GUI routes.

### Judge compatibility

Fresh installations do not expose selectable built-in Judge definitions. New LLM-Judge testcases select a user-created published Judge version. Historical testcases and runs that reference legacy presets remain readable and executable through private compatibility support; they are not silently converted.

New and editable Judges expose ten supported metrics. Historical snapshots containing removed metrics remain readable. Published Judge versions are immutable, and semantic changes require publishing a new version.

### Upgrade notes

Database migrations 18 and 19 add versioned Judge-definition storage and backfill compatible historical semantics. Normal startup creates a pre-upgrade backup before migrating an existing database. Keep the database, WAL, backups, exports, and application on a local filesystem.

Before upgrade:

1. Stop the existing application process.
2. Create and verify a database backup.
3. Install or build version 0.2.0.
4. Start exactly one application process and allow migrations to finish.
5. Verify existing golden sets, historical runs, Judge profiles, and published Judge choices.

No down migrations are provided. To roll back, stop 0.2.0, select the pre-upgrade backup as a new `DATABASE_PATH`, and run the previous package or image. Retain failed-upgrade files for diagnosis.

### Supported boundary

Thistlewarden 0.2.0 supports one trusted user on one host. Keep the application bound to loopback. It does not provide user authentication, tenant isolation, or supported LAN/internet deployment. Provider calls remain server-side. SQLite and backups require a local filesystem.

### Known limitations

- Large run exports are assembled in memory ([#20](https://github.com/lmeulen/Thistlewarden/issues/20)).
- Export SQL identifiers do not yet use an explicit allowlist ([#21](https://github.com/lmeulen/Thistlewarden/issues/21)).
- Provider destination checks do not pin the resolved IP into the HTTP transport ([#22](https://github.com/lmeulen/Thistlewarden/issues/22)).
- `per_metric` Judge calls are sequential and can increase latency and cost in proportion to selected metrics and parse retries.
- Legacy Judge preset compatibility remains internal and is not selectable for new testcases.

[0.2.0]: https://github.com/lmeulen/Thistlewarden/releases/tag/v0.2.0
[0.1.0]: https://github.com/lmeulen/Thistlewarden/releases/tag/v0.1.0
