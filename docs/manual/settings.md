<a id="page-top"></a>

# Application settings

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

## Editable settings

Open **Settings** to see each setting's active value, source, lifecycle, and available action. Only non-secret settings not controlled by environment can be edited. Invalid value or invalid combination is rejected without partial save.

Three lifecycles matter:

- **Bootstrap** — read-only; change environment/configuration file and restart.
- **Restart required** — saved as desired value; active process keeps old value until restart.
- **Dynamic** — applied safely to running process before save completes.

Current dynamic settings are `LOG_LEVEL` and `MAX_MODEL_ANSWER_CHARS`. Other editable settings require restart. Bootstrap settings—including host, port, database/export paths, process count, worker/container mode, and application secrets—cannot be changed in GUI.

Common editable groups, when not environment-controlled, include provider timeout/retry defaults, run concurrency and retention, content-size limits, import/provider-response limits, and maximum model profiles per run.

Values have strict types and ranges. Boolean values accept `true`/`false`, `yes`/`no`, `on`/`off`, or `1`/`0`. Concurrency and size limits must be positive; retry count and retention days may be zero. Keep `DEFAULT_RUN_CONCURRENCY` and `RUN_CONCURRENCY` no greater than `MAX_RUN_CONCURRENCY`.

Before changing a limit, identify expected workload and failure mode. Higher answer/import/provider limits increase local storage and memory exposure; higher concurrency increases provider usage and rate-limit pressure. Test changes with small controlled run.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

## Read-only environment settings

Effective source order is **environment**, then saved database value, then default. `.env` overrides same process-environment names. When source is **environment**, GUI shows setting as read-only because local save cannot override it.

To change environment-controlled value:

1. Wait for active runs to finish when possible.
2. Back up current configuration and record old value.
3. Edit `.env` or deployment configuration.
4. Restart application.
5. Open **Settings** and verify active value and source.
6. Run small controlled check.

Keep host loopback-only. `CONTAINER_MODE=true` permits internal container bind to `0.0.0.0`; supported Compose mapping still publishes host `127.0.0.1` only. Do not add network-accessible publication.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

## Secret handling

`SECRET_KEY` and `ENCRYPTION_KEY` are bootstrap secrets. Settings page and API never display or edit them.

- `SECRET_KEY` protects browser session and CSRF state. Changing it invalidates existing browser state.
- `ENCRYPTION_KEY` encrypts endpoint API keys and headers marked secret. Losing it makes stored provider secrets unreadable; exposing it with database defeats at-rest protection.

Generate independent strong values, store them in `.env` or approved secret mechanism, never commit them, and preserve protected recovery copy. Provider API keys remain managed under endpoint configuration and are separate from application keys. Configuration exports intentionally omit all secret values.

Never place secrets in non-secret settings, URLs, names, descriptions, prompts, expected answers, evaluator configuration, Judge instructions, run notes, filenames, screenshots, tickets, or source control.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

## Settings that require a restart

Settings page marks restart-required values and shows pending desired value. Save does not reconfigure already constructed workers, providers, limits, or services.

Safe restart procedure:

1. Wait until runs reach terminal status. If stopping active work is unavoidable, record run IDs and expect possible recovery or duplicate provider cost.
2. Back up database before storage, retention, or broad operational change.
3. Save desired GUI value or edit environment configuration.
4. Stop and start application using normal installation command.
5. Confirm application readiness and active values in **Settings**.
6. Inspect interrupted run status and complete small test before larger evaluations.

Dynamic `LOG_LEVEL` and `MAX_MODEL_ANSWER_CHARS` do not need restart. Bootstrap values always need process restart. Invalid environment combinations prevent startup; restore previous values or correct error, then start again.

See [Configuration reference](../configuration.md) for complete precedence, topology, retry, and shutdown contracts.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)
