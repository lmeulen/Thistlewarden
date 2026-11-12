# Configuration reference

Technical names remain English; GUI labels are Dutch.

## Source precedence and lifecycle

For restart-required and dynamic settings, effective order is `.env > GUI/database > default`. `.env` is loaded with `override=True`, so file values replace existing process environment. Environment-backed settings are read-only in GUI/API.

Every setting has one explicit lifecycle:

- **Bootstrap:** environment/config-file only and always read-only in GUI/API. Database values are ignored because these settings locate or initialize the database and process. This includes `FLASK_ENV`, `SECRET_KEY`, `ENCRYPTION_KEY`, `HOST`, `PORT`, `DATABASE_PATH`, `EXPORT_PATH`, `APP_PROCESS_COUNT`, `STORAGE_BACKEND`, `WORKERS_ENABLED`, `RELOADER_ENABLED`, and `CONTAINER_MODE`.
- **Restart required:** GUI changes persist as desired configuration but do not change current process or constructed services. `/instellingen` shows active and desired values separately and marks differences pending. Restart process to activate them.
- **Dynamic:** changes are applied through an explicit thread-safe runtime callback before persistence. `LOG_LEVEL` and `MAX_MODEL_ANSWER_CHARS` are dynamic; answer-limit changes atomically update active evaluation-preview service.

All non-bootstrap settings except `LOG_LEVEL` and `MAX_MODEL_ANSWER_CHARS` are restart-required. `GET /api/v1/settings` reports `active_value`, `desired_value`, `lifecycle`, and `pending_restart`; compatibility field `value` is always active value. Secrets remain suppressed in every value field.

`SECRET_KEY` is mandatory and must be a non-default random value of at least 32 UTF-8 bytes. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`, store it in the deployment secret mechanism or `.env`, and never commit it. Changing it invalidates all existing browser sessions and CSRF tokens, requiring users to reload and sign in again if authentication is configured. Startup errors identify `SECRET_KEY` but never include its value. `.env.example` intentionally contains a rejected placeholder.

## Runtime topology

| Variable | Constraint |
|---|---|
| `HOST` | `127.0.0.1`, `::1`, or `localhost`; `0.0.0.0` only with `CONTAINER_MODE=true` |
| `APP_PROCESS_COUNT` | exactly `1` |
| `STORAGE_BACKEND` | exactly `local` |
| `RELOADER_ENABLED` | `false` when workers enabled |
| `RUN_CONCURRENCY` | positive, no greater than `MAX_RUN_CONCURRENCY` |
| `MAX_MODEL_PROFILES_PER_RUN` | default 20 |

Compose enforces one app service, one named local volume and host-loopback publication. Network filesystems, shared volumes and additional app replicas are unsupported.

## Provider retries

Each provider call makes at most `max_retries + 1` physical attempts. Rate-limit responses, provider server errors, and timeout/network failures before any response body data are retryable. Every physical attempt is logged with endpoint and attempt number. After any streaming response data arrives, transport failures return a partial failed result and are never replayed automatically, preventing duplicate execution and billing.

## Shutdown budget

Worker shutdown has one global 30-second deadline inside Compose's 40-second stop grace period. Shutdown signals limiter/retry waits, closes lifecycle-owned provider transports to interrupt blocked responses, and then joins all workers against same absolute deadline. Interrupted jobs remain leased rather than being recorded as provider failures; normal lease-expiry recovery makes them available after restart. A missed deadline is logged as `worker_shutdown_timed_out` and `shutdown_incomplete`; daemon worker threads prevent blocked third-party transports from extending process lifetime beyond external grace period.

## Configuration bundle schema 1.0

Secret-free bundles contain:

- `schema_version`: exactly `"1.0"`;
- `endpoints`;
- `model_profiles` referencing endpoint UUIDs;
- `judge_profiles` referencing model UUIDs;
- `golden_sets` optionally referencing judge UUIDs;
- `golden_set_items` referencing golden-set UUIDs.

Every object has globally unique UUID `id` and object-specific `data`. Secret values are always `null`; importing secret metadata does not recreate secret. Enter secret through GUI after import. `merge` remaps colliding UUIDs. `replace` archives active historical configuration rather than deleting run references.

Machine-valid example: `examples/release-configuration.json`.

## Golden-set JSON

Canonical standalone golden-set JSON contains `name`, optional `description`, `version`, `tags`, and `items`. Each item contains case ID, prompt, optional system/expected text, evaluator method/config, category, tags, Decimal weight and enabled state.

Supported evaluator methods:

- `exact_match`
- `contains`
- `regex`
- `numeric`
- `json_schema` (Draft 2020-12)
- `llm_judge`

JSON is lossless canonical format. CSV stores configs/tags as JSON strings and is intended for tabular editing.
