# Security policy and deployment boundary

## Supported boundary

Thistlewarden is a local, single-user application. Supported deployment is one process on one host, listening only on loopback from host perspective, with SQLite and exports on a local filesystem. Docker Compose publishes container port only on `127.0.0.1`.

Do not expose application directly to LAN or internet. Authentication, tenant isolation, reverse-proxy trust and hostile administrators are outside current threat model.

## Sensitive data

API keys and secret endpoint headers use AES-256-GCM in SQLite. `ENCRYPTION_KEY` and `SECRET_KEY` must be generated independently, stored in `.env`, excluded from source control and backed up separately. Losing encryption key makes encrypted provider secrets unrecoverable.

Prompts, expected answers, model answers and judge details are intentionally plaintext in local SQLite and run exports. Treat database, backups and exports as sensitive. Run downloads require explicit GUI confirmation.

Generate keys:

```bash
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Provider endpoint policy

HTTPS is required by default. HTTP is accepted only for localhost or explicitly confirmed trusted internal endpoints. Redirects and ambient HTTP proxy variables are disabled. DNS destinations are classified before each real call; private, metadata, link-local, multicast, reserved and unspecified addresses require explicit internal trust.

Residual: resolved IP is not pinned into HTTP transport. DNS rebinding between policy resolution and connect remains theoretically possible. Do not allow untrusted users to configure endpoints.

## Backups and restore

Stop application or use SQLite backup command:

```bash
python -m evaluator.cli backup ./backups/manual.db
python -m evaluator.cli restore ./backups/manual.db ./data/restored.db
```

Back up `.env` separately using OS secret storage. Restore always targets a new path. Validate with `migrate`, point `DATABASE_PATH` to restored file, then start app. Roll back by stopping app and restoring pre-upgrade backup to a new local path.

## Reporting

Report vulnerabilities privately to repository owner. Include affected version, reproduction without real secrets, impact and suggested mitigation. Never attach production databases, exports, prompts or provider credentials.
