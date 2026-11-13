# Application settings, security, and privacy

[Manual contents](README.md)

## 15. Application settings

### 15.1 Editable settings

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

[Back to top](README.md#manual-top)

### 15.2 Read-only environment settings

Resolution precedence is **environment**, then saved database value, then built-in default. Settings page labels each source. Any setting supplied as environment variable is read-only in browser because environment value wins over database value.

This protects deployment configuration from accidental UI changes. To change environment-controlled setting, change deployment environment or Compose configuration, then restart application. Do not expect saving a database value to override environment variable.

Environment control is appropriate for host/port, paths, process behavior, resource limits, and all production operator policy. Review source column when a Save control is absent: **environment** means change belongs to operator configuration; **default** means application has no saved override; **database** means prior UI/API setting is stored locally.

Avoid setting `HOST` to network-accessible address outside documented container deployment. Local browser access is security boundary. `CONTAINER_MODE=true` permits internal container bind to `0.0.0.0`; Compose should still publish service only through intended loopback mapping.

[Back to top](README.md#manual-top)

### 15.3 Secret handling

`SECRET_KEY` and `ENCRYPTION_KEY` are secrets. Settings page never displays them and never provides browser editing. API settings output also hides secret values. Configure them through secure environment/deployment secret mechanism, not copied into screenshots, tickets, exports, or source control.

`SECRET_KEY` protects browser-session and CSRF security. `ENCRYPTION_KEY` protects locally stored endpoint secret records. Use strong, unique values and retain protected recovery copy. Changing or losing encryption key can prevent reading existing encrypted endpoint credentials; plan key rotation and credential re-entry with operator procedure.

Provider API keys and secret additional headers are managed with endpoint configuration and encrypted local secret storage; they are separate from application settings. Configuration exports intentionally omit secret values. See [chapter 18](#18-security-and-privacy) and [section 14.5](import-export.md#145-exporting-and-importing-application-configuration).

Never put secrets in non-secret setting fields, golden sets, prompts, evaluator configuration, run notes, judge templates, or exported filenames. Operational logs are designed to avoid sensitive payloads, but prevention at data entry remains required.

[Back to top](README.md#manual-top)

### 15.4 Settings that require a restart

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

[Back to top](README.md#manual-top)

## 18. Security and privacy

### 18.1 Local-only access model

Thistlewarden is designed for use from browser on same computer. Default host is loopback, and browser requests accept only `localhost`, `127.0.0.1`, or `::1` Host and Origin values. This prevents normal browser access through arbitrary network hostnames.

Do not expose application directly to LAN, Internet, reverse proxy, shared workstation, or untrusted users. Application does not provide multi-user authentication or authorization model for remote deployment. If organizational access beyond local machine is required, involve qualified security and platform operators to design authentication, authorization, TLS termination, network isolation, and data controls before deployment.

Container operation can bind process to `0.0.0.0` inside container, but Compose publishes service through loopback by default. This does not change local-browser security boundary. Do not change port publishing to a public interface without formal security review.

Browser protections include strict local Host/Origin validation for mutations, session-bound CSRF tokens on GUI write actions, restrictive Content Security Policy with per-request script nonces, no-store responses, frame denial, and restrictive browser permissions. These are defense layers, not substitute for securing computer and local user account.

[Back to top](README.md#manual-top)

### 18.2 Protecting API keys

Store provider credentials only in endpoint **API key** field or additional header marked `secret`. Secret values are encrypted at rest using authenticated AES-256-GCM encryption and are not displayed after save. Configuration export deliberately omits secret values.

Protect encryption-key configuration as carefully as provider keys. Anyone who obtains both application data and active encryption key may be able to decrypt stored secrets. Restrict filesystem, environment, container-runtime, backup, and operator access. Use dedicated least-privilege provider credentials, rotate them according to policy, and revoke immediately after suspected exposure.

Never put credentials in URLs, model IDs, display names, descriptions, prompts, expected answers, tags, evaluator configuration, judge rubrics, test-output previews, notes, CSV/JSON files, screenshots, tickets, or source control. When editing endpoint, leave existing secret fields blank to preserve current saved value; enter a value only to replace it or create a new secret.

Use HTTPS for external providers. Plain HTTP is automatically allowed for loopback endpoints only. A non-loopback HTTP endpoint requires explicit trusted-internal acknowledgement because it can expose prompts, answers, and credentials in transit.

[Back to top](README.md#manual-top)

### 18.3 Sensitive prompt and answer data

Treat prompts, system prompts, expected answers, candidate answers, judge prompts, rubrics, evaluator configuration, and run notes as potentially confidential. Test and run content is stored locally and can be visible on result-detail pages or in exports. Model providers and LLM judges receive content needed for their configured calls.

Minimize data before it reaches application or provider:

- Use synthetic or redacted test cases where possible.
- Do not include passwords, access tokens, personal data, production customer records, or private keys in test material.
- Send content only to approved providers and judge models under your organization's data-processing terms.
- Limit access to computer, browser profile, database, backups, and exported files.
- Define retention and deletion practices for test suites and runs.

Review model and judge endpoint choice before running LLM-judge cases. A judge can receive prompt, candidate answer, expected answer, and evaluation context; use deterministic evaluation when it meets requirement and avoids sending material to extra model.

[Back to top](README.md#manual-top)

### 18.4 Import and export precautions

Imports are untrusted input. Obtain golden-set and configuration files only from trusted sources, preview before commit, and review provider URLs, active state, model configuration, evaluator settings, pricing, and judge prompts. Do not import files containing credentials; configuration imports do not restore secret values anyway.

Exports can contain sensitive test and result content. Golden-set exports include test material. Run exports may include prompts, expected answers, candidate answers, evaluation data, metrics, and costs. Store exports in approved encrypted locations, restrict recipients, and delete temporary downloads when no longer needed. Never commit them to public or shared source repositories.

Configuration exports omit API keys and secret-header values, but still contain operational information such as endpoint URLs, model settings, and test material. Treat them as confidential where appropriate. Before sharing any export, inspect it and apply organization-approved redaction process.

Backup copies need same or stronger protection as live data. Preserve encryption-key material needed for recovery separately and securely; loss of key can make encrypted secrets unrecoverable, while exposure defeats at-rest protection.

[Back to top](README.md#manual-top)

### 18.5 Operational logging guarantees

Operational logs are designed to record safe diagnostics and lifecycle events without recording sensitive evaluation content. Logging redacts configured secrets and avoids prompts, answers, expected answers, rubrics, provider response bodies, API keys, secret-header values, cookies, CSRF tokens, query strings, complete import/export content, exception messages, and tracebacks.

Request diagnostics use route templates rather than concrete request paths. Server generates request IDs for correlation; do not depend on caller-supplied `X-Request-ID` values. Service events use safe metadata such as object identifiers, method names, and counts after successful committed operations.

These safeguards reduce accidental leakage in application logs, but are not authorization or data-loss-prevention system. Keep log files access-controlled and encrypted according to policy. When reporting issue, provide generated request ID, time, non-sensitive action, and status—not copied prompts, outputs, credentials, or full exports.

[Back to top](README.md#manual-top)

[Manual contents](README.md)
