# Changelog

Notable changes to Thistlewarden are documented here. The project follows semantic versioning after the first stable release; `0.x` releases may contain breaking changes documented in release notes.

## [Unreleased]

No unreleased changes documented yet.

## [0.1.0] - 2026-11-14

First public beta.

### Highlights

- Local evaluation runs across one or more OpenAI-compatible model profiles.
- Versioned golden sets with deterministic exact-match, contains, regex, numeric, and JSON Schema evaluators.
- Immutable, versioned LLM-judge presets with separate judge execution profiles.
- Streaming latency, time-to-first-token, throughput, token, and configured cost metrics.
- Quality gates, model comparisons, detailed result auditing, and JSON, CSV, and HTML exports.
- Configuration import and export, encrypted provider credentials, database backups, and migration safeguards.
- Docker Compose and native Python 3.12 installation paths.

### Supported boundary

Thistlewarden 0.1.0 supports one trusted user on one host. Keep the application bound to loopback. It does not provide user authentication, tenant isolation, or supported LAN/internet deployment.

### Known limitations

- Large run exports are assembled in memory ([#20](https://github.com/lmeulen/Thistlewarden/issues/20)).
- Export SQL identifiers do not yet use an explicit allowlist ([#21](https://github.com/lmeulen/Thistlewarden/issues/21)).
- Provider destination checks do not pin the resolved IP into the HTTP transport ([#22](https://github.com/lmeulen/Thistlewarden/issues/22)).

[Unreleased]: https://github.com/lmeulen/Thistlewarden/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/lmeulen/Thistlewarden/releases/tag/v0.1.0
