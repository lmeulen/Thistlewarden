# Configuration reference

Technical names remain English; GUI labels are Dutch.

## Source precedence and lifecycle

For restart-required and dynamic settings, effective order is `.env > GUI/database > default`. `.env` is loaded with `override=True`, so file values replace existing process environment. Environment-backed settings are read-only in GUI/API.

Every setting has one explicit lifecycle:

- **Bootstrap:** environment/config-file only and always read-only in GUI/API. Database values are ignored because these settings locate or initialize the database and process. This includes `FLASK_ENV`, `SECRET_KEY`, `ENCRYPTION_KEY`, `HOST`, `PORT`, `DATABASE_PATH`, `EXPORT_PATH`, `APP_PROCESS_COUNT`, `STORAGE_BACKEND`, `WORKERS_ENABLED`, `RELOADER_ENABLED`, and `CONTAINER_MODE`.
- **Restart required:** GUI changes persist as desired configuration but do not change current process or constructed services. `/instellingen` shows active and desired values separately and marks differences pending. Restart process to activate them.
- **Dynamic:** changes are applied through an explicit thread-safe runtime callback before persistence. `LOG_LEVEL` and `MAX_MODEL_ANSWER_CHARS` are dynamic; answer-limit changes atomically update active evaluation-preview service.

All non-bootstrap settings except `LOG_LEVEL` and `MAX_MODEL_ANSWER_CHARS` are restart-required. `GET /api/v1/settings` reports `active_value`, `desired_value`, `lifecycle`, and `pending_restart`; compatibility field `value` is always active value. Secrets remain suppressed in every value field.

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
