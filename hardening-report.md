# Step 29 hardening report

Date: 2026-11-06

## Scope and runtime

Supported topology remains one OS host, one Python process, bounded worker threads, local SQLite filesystem. Measurements ran on Windows through Python 3.12.10 in project virtual environment. Exact CPU/storage model was not queried by test code; results are release-machine observations, not portable capacity claims.

## Security controls

- Process-lifetime cross-platform database instance lock is acquired before migrations/recovery and held until workers and HTTP server stop.
- Loopback bind validation is complemented by runtime `Host` and mutation `Origin` checks.
- CSP uses per-request nonces. Inline event handlers were removed. Frame, MIME-sniffing, referrer, permissions, opener/resource and no-store headers are set.
- Global request limit includes 64 KiB multipart framing headroom. Import service independently enforces exact uploaded-file limit.
- Provider clients disable ambient proxy variables and redirects. Every real request resolves destination again; private, loopback, link-local, multicast, unspecified, reserved and metadata addresses require localhost or explicit trusted-internal confirmation.
- Known SSRF residual risk: DNS is classified before request but system resolver and HTTP transport do not share a pinned IP. DNS rebinding between resolution and connect is theoretically possible. Redirects remain disabled. A custom pinning transport is required before exposing this local-only app to untrusted endpoint administrators.
- Cancellation now invalidates heartbeat, attempt and final-result fencing through run-status predicates.
- Readiness checks database integrity and obtains a write transaction; liveness stays database-independent.
- Secrets remain excluded from snapshots/exports and structured logging redaction remains active.

## Robustness and lifecycle

- Production entrypoint starts fixed worker threads only after lock, migrations and lease recovery.
- Worker exceptions are isolated per iteration. Idle waits are interruptible. Shutdown stops workers before limiter and instance-lock release.
- Active provider calls rely on endpoint timeout. Shutdown returns failure if bounded wait expires; leases then expire and startup recovery exposes duplicate-charge warning.
- Migration 11 adds indexes for run/status/model and run/case result paths plus snapshot joins.
- Result GUI uses allowlisted SQL sorting and pages at 100 rows; one look-ahead row determines next-page presence.

## Measurements and gates

Automated hardening scenario inserts 10,000 jobs/results, then enforces:

- first result page (101 rows including look-ahead): under 2.0 seconds;
- full aggregate calculation for 10,000 results: under 3.0 seconds;
- fixed worker count and idle shutdown: under 1 second in unit test;
- blocked worker shutdown timeout is observable and subsequent stop succeeds.

Observed targeted hardening suite: 36 passed in 91.05 seconds. Most time was fixture insertion under SQLite `synchronous=FULL`; query gates passed. Full suite after hardening initially reached 347 passed with one expected-status regression caused by multipart framing; policy was corrected to preserve exact service-level 100,000-byte rejection behavior.

## Verification coverage

Tests cover host/origin edge cases, CSP nonce binding, upload ceiling, readiness, real cross-process lock contention, DNS mixed public/private blocking, metadata/private ranges, explicit trusted internal endpoints, cancellation vs final commit, 10,000-result query gates, worker bounds/shutdown, migrations and existing XSS escaping/import limits. Ruff, formatter and strict mypy are release gates.

## Accepted residuals

- Local-only app is not hardened for reverse proxies or remote multi-user deployment.
- TLS certificate and DNS trust remain operating-system concerns.
- SQLite/network filesystems and multiprocess servers remain unsupported.
- Running call cannot be force-killed safely; lease expiry/recovery handles abrupt process shutdown.
