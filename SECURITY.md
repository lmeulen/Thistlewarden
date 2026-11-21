# Security policy and deployment boundary

## Supported versions

| Version | Supported |
| --- | --- |
| Latest 0.3.x beta | Yes |
| Earlier releases | No |

Security fixes are provided only for latest published 0.3.x beta. Upgrade before reporting behavior already addressed in newer release.

## Supported boundary

Thistlewarden is a **local, single-user application**. Supported deployment has all following properties:

- one trusted user;
- one application process on one host;
- host-facing listener bound only to loopback;
- SQLite, WAL files, exports, and backups on local filesystem; and
- browser used on same trusted computer.

Docker Compose may bind `0.0.0.0` inside container, but supported Compose file publishes port only on host `127.0.0.1`.

Do not expose Thistlewarden directly to LAN, internet, reverse proxy, shared workstation, or untrusted users. Application has no authentication, authorization, tenant isolation, or supported remote-deployment model. Reverse-proxy trust, hostile local administrators, and multi-user isolation are outside threat model.

## Sensitive data

### Application and provider secrets

Provider API keys and endpoint headers marked secret are encrypted in SQLite with AES-256-GCM. Encryption at rest protects those fields only; it does not make host safe after compromise.

`ENCRYPTION_KEY` and `SECRET_KEY` must be independent random values. Store them in `.env` or approved deployment secret mechanism, exclude them from source control, restrict access, and preserve protected recovery copy. Losing `ENCRYPTION_KEY` makes stored provider secrets unrecoverable. Anyone with both database and encryption key may decrypt them. Changing `SECRET_KEY` invalidates existing browser session and CSRF state.

Generate independent values:

```bash
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Evaluation content

Prompts, expected answers, model answers, Judge instructions, evaluator configuration, run notes, and run exports are plaintext in local SQLite or files. Target providers receive prompts; Judge providers may additionally receive candidate answers, expected answers, context, and assessment criteria.

Treat database, `.env`, backups, exports, browser profile, and screenshots as sensitive. Use synthetic or redacted test material where possible. Never place credentials, private keys, production secrets, or unapproved personal data in prompts or evaluator fields.

## Provider endpoint policy

HTTPS is required by default. HTTP is accepted automatically only for localhost/loopback, or after explicit confirmation for trusted internal endpoint. Plain HTTP can expose credentials, prompts, and answers in transit.

Redirects and ambient HTTP proxy variables are disabled. Before each real provider call, destination is resolved and classified. Private, metadata, link-local, multicast, reserved, and unspecified destinations require explicit internal trust.

Residual risk: resolved IP is not pinned into HTTP transport. DNS rebinding between policy resolution and connection remains theoretically possible. Only trusted user may configure endpoints; do not use untrusted endpoint URLs.

## Backups and restore

Before migration, upgrade, or direct database work, stop application or create consistent SQLite backup:

```bash
python -m evaluator.cli backup ./backups/manual.db
python -m evaluator.cli restore ./backups/manual.db ./data/restored.db
```

Restore always targets new path. Database backup alone is insufficient for complete recovery: preserve `.env` or deployment secrets separately so encrypted endpoint credentials remain readable. Protect backups at least as strongly as live data.

Test recovery in isolated local environment:

1. Restore database to new local path.
2. Supply matching protected `ENCRYPTION_KEY` and valid `SECRET_KEY`.
3. Set `DATABASE_PATH` to restored path.
4. Run migration/startup validation.
5. Confirm records are readable and re-test provider access with approved credential.

Never send production database, `.env`, backup, or unredacted export with support report.

## Reporting a vulnerability

Do not open public issue for suspected vulnerability. Use repository's private **Security → Report a vulnerability** form. If unavailable, contact repository owner through private contact method on GitHub profile and ask for secure channel before sharing details.

Include:

- affected Thistlewarden version;
- minimal reproduction using synthetic data and no real secrets;
- security impact and deployment assumptions; and
- suggested mitigation, if known.

Do not attach credentials, cookies, CSRF values, production databases, backups, prompts, model answers, provider responses, or unredacted exports.

Expected acknowledgement is within seven days. Maintainer validates report, coordinates remediation and disclosure, and credits reporter unless anonymity is requested.
