# Configuration reference

This operator reference explains configuration sources, when changes take effect, supported runtime shape, and exchange-file contracts. Application settings and GUI labels are English.

For ordinary use, copy `.env.example`, set independent `SECRET_KEY` and `ENCRYPTION_KEY`, keep host loopback-only, and leave topology defaults unchanged.

## Source precedence and lifecycle

Effective source order is:

```text
.env / process environment > saved GUI/database value > built-in default
```

`.env` is loaded with `override=True`, so its values replace same process-environment names. Settings controlled by environment are read-only in GUI/API.

Each setting has one lifecycle:

| Lifecycle | Where changed | When effective |
|---|---|---|
| **Bootstrap** | Environment or configuration file only | Next process start |
| **Restart required** | GUI/database unless environment-controlled | After process restart |
| **Dynamic** | GUI/API unless environment-controlled | Immediately after validated save |

Bootstrap settings locate or initialize process and database; database values cannot control them. They are `FLASK_ENV`, `SECRET_KEY`, `ENCRYPTION_KEY`, `HOST`, `PORT`, `DATABASE_PATH`, `EXPORT_PATH`, `APP_PROCESS_COUNT`, `STORAGE_BACKEND`, `WORKERS_ENABLED`, `RELOADER_ENABLED`, and `CONTAINER_MODE`.

Dynamic settings are `LOG_LEVEL` and `MAX_MODEL_ANSWER_CHARS`. All other non-bootstrap settings are restart-required. Settings page shows active value, source, lifecycle, and pending desired value where applicable. `GET /api/v1/settings` reports `active_value`, `desired_value`, `lifecycle`, and `pending_restart`; compatibility field `value` is active value. Secret values are always suppressed.

`SECRET_KEY` is mandatory, non-default, and at least 32 UTF-8 bytes. Generate independently from encryption key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Store securely and never commit it. Changing it invalidates existing browser session and CSRF state. Startup errors name setting but never value. `.env.example` contains intentionally rejected placeholder.

## Runtime topology

Supported topology is one process, one host, local filesystem, and host-loopback access.

| Variable | Supported constraint |
|---|---|
| `HOST` | `127.0.0.1`, `::1`, or `localhost`; `0.0.0.0` only inside container when `CONTAINER_MODE=true` |
| `APP_PROCESS_COUNT` | Exactly `1` |
| `STORAGE_BACKEND` | Exactly `local` |
| `RELOADER_ENABLED` | `false` while workers enabled |
| `RUN_CONCURRENCY` | Positive and no greater than `MAX_RUN_CONCURRENCY` |
| `MAX_MODEL_PROFILES_PER_RUN` | Default `20` |

Compose enforces one application service, one named local volume, and host-loopback publication. Do not add application replicas, shared database volume, network filesystem, or host-wide port publication. `CONTAINER_MODE=true` does not authorize network exposure; it only permits internal container bind.

Paths are resolved in runtime context. Keep database, WAL, export, temporary export, and backup paths on same trusted local filesystem class. Back up database and deployment secrets separately.

## Health and readiness probes

`GET /health` is process liveness only. It always returns `200` while Flask can serve requests and does not open database.

`GET /ready` is container readiness. Each probe opens database read-only with a one-second SQLite busy bound and reads latest `schema_migrations` version. It returns `200 {"status":"ready"}` only when local storage is readable and schema matches application-supported version. It does not run `quick_check`, run an integrity scan, or acquire an immediate write lock, so ten-second Docker and Compose probes do not compete with application writers.

A non-ready response is `503` with one reason: `database_unavailable` when runtime has no initialized database, `storage_unavailable` when database cannot be opened or read, or `schema_unsupported` when migration version differs. Startup migrations, backup creation, and restore retain full `PRAGMA integrity_check` validation. Diagnose non-ready storage or schema state before restarting container; do not treat `/health` as storage health.

## Provider retries

Each provider call makes at most `max_retries + 1` physical attempts. Retry-eligible conditions are provider rate limits, temporary provider-server errors, and timeout/network failure before response body data arrives.

Streaming safety rule: after any response body data arrives, transport failure returns partial failed result and is never replayed automatically. This avoids silently repeating partially completed request and possible duplicate billing.

Retries can still add latency and provider usage. Set endpoint retry and rate-limit values according to provider contract; they do not increase provider quota.

## Internal worker diagnostics

Unexpected worker defects log an `ERROR` event with correlation IDs, exception type, and bounded file/function/line traceback frames. Exception messages, source lines, locals, prompts, answers, provider payloads, credentials, and secret identifiers are excluded. Identical traceback signatures log at most once per minute to prevent repeated systemic failures from flooding logs. Expected provider, cancellation, lease-loss, guardrail, and Judge failures retain their classified result handling without internal-defect tracebacks.

## Shutdown budget

Worker shutdown uses one global 30-second deadline within Compose's 40-second stop grace period. Shutdown signals limiter/retry waits, closes lifecycle-owned provider transports, then joins workers against same absolute deadline.

Interrupted jobs remain leased rather than being recorded as provider failures. After restart, normal lease-expiry recovery makes them available again. Provider may already have completed interrupted call, so recovery can repeat work and create duplicate cost. Prefer stopping only after active runs reach terminal status.

A missed deadline logs `worker_shutdown_timed_out` and `shutdown_incomplete`. Daemon worker threads prevent blocked third-party transport from extending process beyond external grace period.

## Configuration bundle schema 1.0

Configuration exchange uses secret-free JSON bundle with:

- `schema_version`: exactly `"1.0"`;
- `endpoints`;
- `model_profiles` referencing endpoint UUIDs;
- `judge_definitions` and immutable published versions;
- `judge_profiles` referencing model UUIDs;
- `golden_sets` optionally referencing Judge-profile UUIDs; and
- `golden_set_items` referencing golden-set UUIDs and exact published Judge versions where applicable.

Every object has globally unique UUID `id` and object-specific `data`. API keys and secret-header values are always `null`/missing and must be entered locally after import. Run history is not part of configuration bundle.

Import behavior:

- **Merge** adds records and remaps colliding incoming UUIDs and references.
- **Replace** archives current active configuration rather than deleting records used by history, then imports bundle.
- Published Judge identities and semantic hashes are validated; conflicting immutable definitions are rejected, not silently rewritten.
- Commit is atomic: entire valid bundle applies or nothing changes.

Preview immediately before commit and review new records, archived records, UUID remaps, immutable-definition conflicts, active states, provider URLs, model prices, and endpoints missing secrets.

Machine-valid example: `examples/release-configuration.json`. Trusted local custom Judge implementation configuration is documented in [Custom LLM Judge extensions](judge-extensions.md).

## Golden-set JSON

Canonical standalone golden-set JSON contains:

- `name`, optional `description`, user-managed `version`, and `tags`;
- optional Judge-profile override and quality-gate defaults where supported by format; and
- ordered `items`.

Each item contains stable case ID, user prompt, optional system prompt, evaluator method/configuration, category, tags, Decimal weight, and enabled state. LLM-Judge items select immutable published Judge ID and exact version; they do not define metrics, weights, threshold, or Judge prompt.

Supported evaluator method identifiers:

- `exact_match`
- `contains`
- `regex`
- `numeric`
- `json_schema` (Draft 2020-12)
- `llm_judge`

JSON is lossless canonical exchange format. CSV stores evaluator configurations and tags as JSON strings and is intended for tabular editing. Both may contain sensitive prompts and expected values; validate and protect files before import or sharing.
