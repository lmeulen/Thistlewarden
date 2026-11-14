<a id="page-top"></a>

# Application settings

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

## Editable settings

Open **Settings** to view operational settings, resolved value, and source. A setting is editable in browser only when it is non-secret and not controlled by environment. Enter replacement value and select **Save**. Application validates value and all setting combinations before saving; invalid change is rejected without partial write.

Editable database-backed settings include, when not environment-controlled:

- `FLASK_ENV`, `HOST`, `PORT`, and `LOG_LEVEL`.
- `DATABASE_PATH`, `EXPORT_PATH`, `APP_PROCESS_COUNT`, and `STORAGE_BACKEND`.
- `WORKERS_ENABLED`, `RELOADER_ENABLED`, and `CONTAINER_MODE`.
- Provider defaults: `DEFAULT_REQUEST_TIMEOUT_SECONDS` and `DEFAULT_MAX_RETRIES`.
- Run settings: `RUN_CONCURRENCY`, `DEFAULT_RUN_CONCURRENCY`, `MAX_RUN_CONCURRENCY`, and `RUN_RETENTION_DAYS`.
- Limits: prompt, system-prompt, expected-answer, model-answer, judge-context, import-file, provider-response, golden-set-item, and model-profiles-per-run limits.

Values have type and range rules. Boolean values accept `true`/`false`, `yes`/`no`, `on`/`off`, or `1`/`0`. Port must be `1` through `65535`; concurrency and size limits must be positive; retry count and retention days may be zero. `STORAGE_BACKEND` must remain `local`. `HOST` must be loopback (`127.0.0.1`, `::1`, or `localhost`), except `0.0.0.0` is allowed only with container mode enabled.

Keep `DEFAULT_RUN_CONCURRENCY` and `RUN_CONCURRENCY` no greater than `MAX_RUN_CONCURRENCY`. Do not enable reloader while workers are enabled. Use Settings for controlled local persistence; use environment configuration when deployment needs immutable operator-owned settings.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

## Read-only environment settings

Resolution precedence is **environment**, then saved database value, then built-in default. Settings page labels each source. Any setting supplied as environment variable is read-only in browser because environment value wins over database value.

This protects deployment configuration from accidental UI changes. To change environment-controlled setting, change deployment environment or Compose configuration, then restart application. Do not expect saving a database value to override environment variable.

Environment control is appropriate for host/port, paths, process behavior, resource limits, and all production operator policy. Review source column when a Save control is absent: **environment** means change belongs to operator configuration; **default** means application has no saved override; **database** means prior UI/API setting is stored locally.

Avoid setting `HOST` to network-accessible address outside documented container deployment. Local browser access is security boundary. `CONTAINER_MODE=true` permits internal container bind to `0.0.0.0`; Compose should still publish service only through intended loopback mapping.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

## Secret handling

`SECRET_KEY` and `ENCRYPTION_KEY` are secrets. Settings page never displays them and never provides browser editing. API settings output also hides secret values. Configure them through secure environment/deployment secret mechanism, not copied into screenshots, tickets, exports, or source control.

`SECRET_KEY` protects browser-session and CSRF security. `ENCRYPTION_KEY` protects locally stored endpoint secret records. Use strong, unique values and retain protected recovery copy. Changing or losing encryption key can prevent reading existing encrypted endpoint credentials; plan key rotation and credential re-entry with operator procedure.

Provider API keys and secret additional headers are managed with endpoint configuration and encrypted local secret storage; they are separate from application settings. Configuration exports intentionally omit secret values. See [Security and privacy](security-and-privacy.md) and [Exporting and importing application configuration](import-export.md#exporting-and-importing-application-configuration).

Never put secrets in non-secret setting fields, golden sets, prompts, evaluator configuration, run notes, judge templates, or exported filenames. Operational logs are designed to avoid sensitive payloads, but prevention at data entry remains required.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

## Settings that require a restart

Saving non-secret database setting updates stored resolution and Settings page immediately. Running application components are initialized from settings at startup, so restart application after any operational setting change to ensure it takes effect consistently.

Restart is especially required after changes to:

- `HOST`, `PORT`, `FLASK_ENV`, `LOG_LEVEL`, `DATABASE_PATH`, or `EXPORT_PATH`.
- `SECRET_KEY`, `ENCRYPTION_KEY`, process count, storage backend, workers, reloader, or container mode.
- Provider timeout/retry defaults, run concurrency, retention, or any content/resource limit.

Safe procedure:

1. Avoid restart while a run is active unless interruption is acceptable.
2. Record old and new values and reason for change.
3. Save database-backed value, or change environment/deployment configuration.
4. Stop and start application using normal operator procedure.
5. Open Settings and verify value and source.
6. Run a small controlled check before resuming production evaluations.

An interrupted provider call may be retried after recovery and can cause duplicate cost. Wait for terminal runs where possible, then inspect readiness and run status after restart. Invalid environment combinations prevent successful startup; correct configuration and restart again.


[Top of page](#page-top) · [Manual contents](README.md#manual-top)
