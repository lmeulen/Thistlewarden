# Thistlewarden 0.2.1 release notes

## [0.2.1] - 2026-11-18

Beta patch release.

### Highlights

- Add an immutable Judge output mode for either strict JSON-only responses or bounded deliberation followed by delimited final JSON.
- Use one core-owned final-assessment boundary and validate only the enclosed JSON; missing, duplicate, reversed, trailing, malformed, oversized, or truncated output fails closed.
- Support both one-assessment and sequential per-metric Judge modes without accepting partial decisions.
- Keep concise deliberation transient: it is not stored in result metadata, displayed, exported, or logged.
- Preserve existing published Judges, historical snapshots, JSON-only parsing behavior, and historical semantic hashes.
- Explain token, latency, cost, and model-compatibility tradeoffs in the Judge editor and user manual.

### Compatibility

Existing Judge definitions and historical snapshots without an `output_mode` remain strict JSON-only. Their semantic hashes do not change. Selecting deliberation for a published Judge requires a new immutable Judge version and produces a distinct semantic hash.

Provider-native private reasoning remains provider-controlled. Deliberation mode asks only for concise visible evidence notes and a self-check before final JSON; it does not request private chain-of-thought.

### Upgrade notes

Version 0.2.1 adds no database migration. Normal startup and rollback procedures remain unchanged.

Before upgrade:

1. Stop the existing application process.
2. Create and verify a database backup.
3. Install or build version 0.2.1.
4. Start exactly one application process.
5. Verify existing published Judges and historical runs remain readable.

To roll back, stop 0.2.1, select the pre-upgrade backup as a new `DATABASE_PATH`, and run version 0.2.0. Retain failed-upgrade files for diagnosis.

### Supported boundary

Thistlewarden 0.2.1 supports one trusted user on one host. Keep the application bound to loopback. It does not provide user authentication, tenant isolation, or supported LAN/internet deployment. Provider calls remain server-side. SQLite and backups require a local filesystem.

### Known limitations

- Large run exports are assembled in memory ([#20](https://github.com/lmeulen/Thistlewarden/issues/20)).
- Export SQL identifiers do not yet use an explicit allowlist ([#21](https://github.com/lmeulen/Thistlewarden/issues/21)).
- Provider destination checks do not pin the resolved IP into the HTTP transport ([#22](https://github.com/lmeulen/Thistlewarden/issues/22)).
- Deliberation and sequential per-metric Judge modes can increase output tokens, latency, and cost.
- Models that cannot reliably emit the exact final-assessment boundary should use strict JSON-only mode.

[0.2.1]: https://github.com/lmeulen/Thistlewarden/releases/tag/v0.2.1
[0.2.0]: https://github.com/lmeulen/Thistlewarden/releases/tag/v0.2.0
