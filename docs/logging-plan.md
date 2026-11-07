# Logging plan

Status: implemented. Core contract, app-wide correlation, semantic mutation events, worker/provider diagnostics, CLI lifecycle events, and leak-focused tests are active. Optional future JSON/file logging remains deferred as described below.

## 1. Purpose and principles

Logging should support local management without replacing database or GUI.

- `INFO`: chronological overview of lifecycle, user changes and run progress in outline.
- `DEBUG`: technical steps within the same action, useful for diagnosis.
- `WARNING`: expected but abnormal condition that may require attention.
- `ERROR`: Action or job failed due to application, storage or provider error.
- `CRITICAL`: Process cannot start or continue safely.

Logs are operational metadata. Prompts, answers, expected answers, rubrics, provider response bodies, API keys, secret headers, cookies, CSRF tokens and complete import/export content are never logged.

## 2. Output and configuration

### Eerste implementatie

Keep one `StreamHandler` to `stderr`. This fits native terminals, systemd and containers.

```text
LOG_LEVEL=INFO
```

- Native visible in active terminal; redirect/Tee for file.
- Docker visible via `docker compose logs -f evaluator`.
- No built-in log file or rotation in phase 1; process manager/container platform manages retention.

Latere optionele uitbreiding:

```text
LOG_FORMAT=text|json
LOG_FILE=<local path or empty>
LOG_FILE_MAX_BYTES=10485760
LOG_FILE_BACKUP_COUNT=5
```

File logging uses only `RotatingFileHandler`, denies network filesystem and has the same editorial filter.

## 3. Vast eventcontract

Elke regel bevat bestaande basisvelden:

```text
timestamp level component request_id run_id case_id execution_id message
```

`message` starts with stable English `snake_case` event name. After that, only allowlisted `key=value` fields. Event names and fields are API-like stable; human Dutch GUI text is not used as event name.

Aanvullende veilige velden:

- request: `method`, route template, `status`, `duration_ms`, `interface=gui|api`;
- object: UUID, technical type, non-sensitive name if necessary;
- import: mode, preview/commit, object counts, conflict and missing-secret counts;
- run: status, jobs total/done/error, model number, case number, gate status;
- provider: endpoint ID, model ID, attempt, status category, latency, TTFT, token counts;
- worker: lease generation, result status, evaluator method, error code.

Do not log:

- URL query string; route parameter can only be UUID;
- request/response body, multipart file name or content;
- endpoint headers or provider headers;
- exception text from provider or parser when it can contain body data;
- user note, prompt, candidate answer, context or schema content;
- local API key, encryption key, session cookie or token.

## 4. Correlatie

1. Assign **every** GUI and API request a `request_id`, not just `/api/v1`.
2. Do not accept arbitrary external request ID as a log field; generate server-side UUID.
3. Bind route known `run_id` with `log_context`.
4. Bind worker claim to `run_id`, `case_id` and new random `execution_id`.
5. Judgecall keeps the same worker context.
6. Provider retrieval preserves context and adds only `attempt`.

Response header `X-Request-ID` is returned for GUI and API. Error page shows request ID so user can find one action.

## 5. INFO-eventcatalogus

INFO shows meaningful changes, not every GET or polling call.

### Proces en beheer

| Event | When | Core fields |
|---|---|---|
| `application_initialized` | database/migrations/config ready | host, port, workers_enabled, database |
| `recovery_completed` | startup recovery performed | recovered_runs, recovered_jobs |
| `worker_runtime_started` | workers started | concurrency |
| `http_server_listening` | server ready | host, port |
| `shutdown_started` / `shutdown_completed` | controlled stop | no sensitive fields |
| `backup_completed` / `restore_completed` | CLI action done | target path, bytes/duration |
| `retention_cleanup_completed` | retention action | deleted_runs |

### Gebruikersacties via GUI en API

Each successful mutation receives exactly one semantic INFO event in service layer, so that GUI and API deliver the same event.

| Area | Events |
|---|---|
| Settings | `setting_updated` with setting name and source, never secret value |
| Endpoints | `endpoint_created`, `endpoint_updated`, `endpoint_deleted`, `endpoint_connection_tested` |
| Discovery | `model_discovery_completed`, `discovered_models_imported` |
| Models | `model_profile_created`, `model_profile_updated`, `model_profile_deleted` |
| Golden sets | `golden_set_created`, `golden_set_updated`, `golden_set_deleted`, `golden_set_import_previewed`, `golden_set_imported`, `golden_set_exported` |
| Cases | `golden_item_created`, `golden_item_updated`, `golden_item_deleted` |
| Judges | `judge_profile_created`, `judge_profile_updated`, `judge_profile_deleted`, `golden_set_judge_changed` |
| Gates | `quality_gates_updated` with only gate names configured |
| Config exchange | `configuration_exported`, `configuration_import_previewed`, `configuration_imported` with numbers |
| Runs | `run_created`, `run_cancel_requested`, `run_failures_rerun`, `run_deleted`, `run_exported` |

Read actions (`GET` page, status polling, lists) do not appear on INFO. This means that INFO remains readable as an audit-like main overview. Authentication doesn't exist, so don't log a made-up user; use `actor=local_gui` or `actor=api`.

### Runuitvoering

| Event | When | Core fields |
|---|---|---|
| `run_started` | first job claims pending run | jobs_total, target_models |
| `run_progress` | maximum per 10 completed jobs or 10% | done, total, errors |
| `run_completed` | terminal run | status, passed, failed, errors, gate_status |
| `run_cancelled` | cancellation effective | done, total |
| `job_failed` | test case ends as error | error_code, evaluator_method |

Success per test case is not listed on INFO; a hundred cases would crowd out the main points.

## 6. DEBUG-eventcatalogus

DEBUG beschrijft stappen, maar nooit inhoud.

### HTTP

- `request_started`: method, route template, interface, content_length.
- `request_completed`: status, duration_ms.
- `csrf_validated`, `origin_validated`, `host_validated`: result/policy only, no token/header value.
- `payload_validated`: model name and object quantity.

Health/readiness and `/runs/<id>/status` are allowed on DEBUG but not INFO.

### Database en transacties

- `transaction_started`, `transaction_committed`, `transaction_rolled_back`: operational name and duration.
- No SQL parameters. Raw SQL only if static and explicitly safe; no logging by default.
- `migration_started`, `migration_applied`, `migration_completed`: version numbers.

### Queue en workers

- `job_claim_started`, `job_claimed`: job-ID, lease generation.
- `lease_heartbeat`, `lease_recovered`, `lease_lost`.
- `case_snapshot_loaded`, `model_snapshot_loaded`: IDs, methode/model-ID.
- `target_call_started`, `target_stream_completed`: endpoint/model, duration, TTFT, bytes, finish reason, usagevelden.
- `evaluation_started`, `evaluation_completed`: methode, score, passed.
- `judge_call_started`, `judge_parse_started`, `judge_parse_retry`, `judge_call_completed`.
- `job_result_committed`, `run_status_refreshed`, `quality_gates_evaluated`.

Heartbeatlogging can be loud. Log only first heartbeat and then a maximum of one every 30 seconds per job, or make it `DEBUG` with sampling.

### Provider en limiter

- `destination_validated`: scheme and classification, not full URL.
- `rate_limit_wait_started/completed`: endpoint ID and wait_ms.
- `provider_attempt_started/completed`: attempt, status code category, duration.
- `provider_retry_scheduled`: error category and backoff_ms.
- `sse_stream_opened/completed`: event_count, byte_count, finish_reason, usage_present.

Geen SSE-chunks, JSON-body of responseheaders loggen.

### Import/export

- parse, schedule, reference and plan steps with object counts;
- no payload fragments;
- in case of validation error, only safe error type, field path and row number if it contains no content.

## 7. WARNING-, ERROR- en CRITICAL-beleid

### WARNING

- provider retry or rate limit backoff;
- expired lease/recovery;
- missing endpoint secret blocks call;
- partial/invalid stream rejected;
- run completed with errors;
- gate status failed/indeterminate;
- shutdown timeout;
- import preview with remaps/conflicts.

### ERROR

Use `logger.exception` only for internal unexpected exceptions. For known errors log event plus safe `error_code` and `exception_type`, not `str(exception)` as provider/payload content can leak.

- definitieve providercallfout;
- jobcommit/queuefencing mislukt;
- databaseoperatie mislukt;
- onverwachte requestfout;
- worker iteration failure.

### CRITICAL

- instance lock not available;
- database migration/integrity check failed;
- invalid bootstrapconfig;
- server cannot bind/start.

Startup errors before `configure_logging()` get a minimal bootstrap handler at `stderr`.

## 8. Implementatiearchitectuur

### Centrale eventhelper

Voeg `log_event(logger, level, event, **safe_fields)` toe in `logging_config.py`.

- allowlist veldnamen en primitieve waarden;
- reject/redact verdachte namen (`secret`, `token`, `key`, `authorization`, `cookie`, `prompt`, `answer`, `body`, `content`);
- begrens elke waarde en hele regel;
- sorteer velden deterministisch;
- converteer UUID/Decimal veilig;
- ondersteun tekst- en toekomstige JSON-formatter vanuit hetzelfde record.

No scattered f-strings with random objects.

### Requestinstrumentatie

Extend `api/handlers.py` to application-wide request correlation:

- `before_request`: request-ID, monotone starttijd, DEBUG request-start;
- `after_request`: duration, responseheader, DEBUG request-end;
- semantische mutatielogs blijven servicelaagverantwoordelijkheid.

### Servicelaag

Post INFO events only after successful transactional commit. Preview events after full validation. Errors are logged once at the boundary layer to avoid duplicate lines.

### Workerlaag

`WorkerPipeline.run_once` binds context and emit DEBUG steps. `RunQueue.refresh_run_status` only emits transition events, not unchanged status. Progress is throttled via database values, not per thread memory alone.

## 9. Prestatie- en volumebudget

For baseline of 100 cases, one target model and judge:

- INFO: less than approximately 30 lines excluding job errors;
- DEBUG: approximately 8–20 lines per job, without chunks/heartbeat spam;
- logcalls should not block provider stream with network I/O;
- text formatting is skipped by logging-level guard where fields are expensive;
- maximum 8 KiB per log line.

## 10. Teststrategie

### Unit

- INFO/DEBUG filtering;
- event field allow list and length limit;
- editing secrets in message, args and extra fields;
- ban on prompt/body/header/cookie fields;
- context bind/reset across threads;
- deterministic text and JSON output;
- exception logging shows type but not sensitive text.

### Integratie

- GUI mutation and equivalent API mutation deliver the same semantic event;
- GET/status polling does not provide INFO;
- request ID is in log and response;
- configuration import preview/commit only logs quantities;
- endpoint with fixturesecret: secret doesn't appear anywhere in logs;
- run of all six evaluators produces lifecycle/progress/gate events;
- retry and failed stream produce safe WARNING/ERROR events;
- parallel workers maintain correct run/case/execution context.

### Releasegate

CI runs E2E with `LOG_LEVEL=DEBUG`, saves stderr and fails when:

- prevent known fixture secrets;
- fixture prompt or candidate response occurs;
- required lifecycle event missing;
- log line is greater than limit;
- traceback prevents successful E2E.

## 11. Gefaseerd implementatieplan

1. **Contract and helper**: `log_event`, allowlists, formatter, editing and tests.
2. **Request correlation**: all GUI/API requests, timing and response header.
3. **Lifecycle**: startup, migrations, recovery, workers, server and shutdown.
4. **User changes**: semantic INFO events in services for settings, endpoints, models, sets, judges, configuration and runs.
5. **Queue/run**: transitions, progress-throttling, cancellation, rerun, retention and gates.
6. **Provider DEBUG**: limiter, retries, DNS classification, streaming metrics and judge parser without content.
7. **CLI**: migrate, backup and restore events.
8. **E2E leak scan and volume test**.
9. **Documentation**: event catalog, troubleshooting, native/Docker capture and retention.
10. **Optional**: JSON format and rotating local file after operational need.

Each phase requires Ruff, strict Mypy, unit/integration tests, and a secret leak scan before the next phase starts.

## 12. Acceptatiecriteria

At `LOG_LEVEL=INFO`, administrator can reconstruct from logs:

- when app started and stopped;
- which local changes have been carried out via GUI/API;
- which run has been started, canceled or completed;
- progress and numbers without test case content;
- which provider/job category failed;
- gate end status.

At `LOG_LEVEL=DEBUG`, the developer can also reconstruct:

- request and worker steps with shared correlation IDs;
- queue claim, lease, provider attempts, streaming metrics, evaluator and commit steps;
- retry/backoff and safe fault classification.

No level reveals secrets or prompt/answer content.
