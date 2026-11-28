# Thistlewarden 0.4.0 release notes

## [0.4.0] - 2026-11-28

Beta feature release.

### Highlights

- Add generic OAuth 2.0 client-credentials authentication for provider endpoints. Token requests use form-encoded client credentials; access tokens remain in process memory and are never persisted.
- Permit OAuth endpoints to retain an optional static API key and additional headers for gateway deployments.
- Cache OAuth tokens per endpoint using monotonic expiry tracking and synchronized refreshes. Token requests enforce existing destination validation, disable redirects, and ignore ambient proxy settings.
- Add OAuth configuration support to endpoint API, OpenAPI schema, configuration import/export, encrypted secret storage, and endpoint overview status.
- Improve endpoint form OAuth layout so token helper text does not overlap fields or adjacent controls.
- Remove endpoint-form HTTP trust and confirmation checkboxes. The form accepts HTTP only for localhost and loopback endpoints; use HTTPS for all other provider and token URLs.

### Compatibility

Database migration 24 adds OAuth endpoint configuration and encrypted OAuth client-secret references. Existing endpoints remain API-key endpoints. Existing databases are upgraded automatically after a pre-upgrade backup is created.

OAuth client secrets and access tokens are not included in exports. OAuth configurations imported without a client secret require that secret to be supplied before use. Existing API-key endpoints and API clients retain their authentication behavior.

### Upgrade notes

Before upgrade:

1. Stop the existing application process.
2. Create and verify a database backup.
3. Install or build version 0.4.0.
4. Start exactly one application process.
5. For OAuth endpoints, enter token URL, client ID, client secret, and optional scope, then test connection and model discovery.

To roll back, stop 0.4.0, select the pre-upgrade backup as the new `DATABASE_PATH`, and run version 0.3.3. No down migrations are provided. Retain failed-upgrade files for diagnosis.

### Supported boundary

Thistlewarden 0.4.0 supports one trusted user on one host. Keep application bound to loopback. It does not provide user authentication, tenant isolation, or supported LAN/internet deployment. Provider calls remain server-side. SQLite, temporary exports, and backups require a local filesystem.

### Known limitations

- Synchronous run exports support at most 50,000 jobs per run; split larger evaluations before export.
- Provider destination checks do not pin resolved IP into HTTP transport ([#22](https://github.com/lmeulen/Thistlewarden/issues/22)).
- OAuth support is limited to client-credentials grants with client ID and client secret form fields.
- Deliberation and sequential per-metric Judge modes can increase output tokens, latency, and cost.
- Models unable to reliably emit an exact final-assessment boundary should use strict JSON-only mode.

[0.4.0]: https://github.com/lmeulen/Thistlewarden/releases/tag/v0.4.0
[0.3.3]: https://github.com/lmeulen/Thistlewarden/releases/tag/v0.3.3
