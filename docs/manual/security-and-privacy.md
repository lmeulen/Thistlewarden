<a id="page-top"></a>

# Security and privacy

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

Before regular use, confirm:

- browser URL uses loopback (`127.0.0.1`, `localhost`, or `::1`);
- application is not published to LAN/internet;
- provider credentials are dedicated and least privilege;
- test material is synthetic, redacted, or approved for selected providers;
- database, `.env`, backups, and exports are access-controlled; and
- retention/deletion responsibilities are defined.

## Local-only access model

Thistlewarden is designed for use from browser on same computer. Default host is loopback, and browser requests accept only `localhost`, `127.0.0.1`, or `::1` Host and Origin values. This prevents normal browser access through arbitrary network hostnames.

Do not expose application directly to LAN, Internet, reverse proxy, shared workstation, or untrusted users. Application does not provide multi-user authentication or authorization model for remote deployment. If organizational access beyond local machine is required, involve qualified security and platform operators to design authentication, authorization, TLS termination, network isolation, and data controls before deployment.

Container operation can bind process to `0.0.0.0` inside container, but Compose publishes service through loopback by default. This does not change local-browser security boundary. Do not change port publishing to a public interface without formal security review.

Browser protections include strict local Host/Origin validation for mutations, session-bound CSRF tokens on GUI write actions, restrictive Content Security Policy with per-request script nonces, no-store responses, frame denial, and restrictive browser permissions. These are defense layers, not substitute for securing computer and local user account.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

## Protecting API keys

Store provider credentials only in endpoint **API key** field or additional header marked `secret`. Secret values are encrypted at rest using authenticated AES-256-GCM encryption and are not displayed after save. Configuration export deliberately omits secret values.

Protect encryption-key configuration as carefully as provider keys. Anyone who obtains both application data and active encryption key may be able to decrypt stored secrets. Restrict filesystem, environment, container-runtime, backup, and operator access. Use dedicated least-privilege provider credentials, rotate them according to policy, and revoke immediately after suspected exposure.

Never put credentials in URLs, model IDs, display names, descriptions, prompts, expected answers, tags, evaluator configuration, Judge instructions, test-output previews, notes, CSV/JSON files, screenshots, tickets, or source control. When editing endpoint, leave existing secret fields blank to preserve current saved value; enter a value only to replace it or create a new secret.

Use HTTPS for external providers. Plain HTTP is automatically allowed for loopback endpoints only. A non-loopback HTTP endpoint requires explicit trusted-internal acknowledgement because it can expose prompts, answers, and credentials in transit.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

## Sensitive prompt and answer data

Treat prompts, system prompts, expected answers, candidate answers, Judge prompts and instructions, evaluator configuration, and run notes as potentially confidential. Test and run content is stored locally and can be visible on result-detail pages or in exports. Model providers and LLM judges receive content needed for their configured calls.

Minimize data before it reaches application or provider:

- Use synthetic or redacted test cases where possible.
- Do not include passwords, access tokens, personal data, production customer records, or private keys in test material.
- Send content only to approved providers and judge models under your organization's data-processing terms.
- Limit access to computer, browser profile, database, backups, and exported files.
- Define retention and deletion practices for test suites and runs.

Review model and judge endpoint choice before running LLM-judge cases. A judge can receive prompt, candidate answer, expected answer, and evaluation context; use deterministic evaluation when it meets requirement and avoids sending material to extra model.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

## Import and export precautions

Imports are untrusted input. Obtain golden-set and configuration files only from trusted sources, preview before commit, and review provider URLs, active state, model configuration, evaluator settings, pricing, and judge prompts. Do not import files containing credentials; configuration imports do not restore secret values anyway.

Exports can contain sensitive test and result content. Golden-set exports include test material. Run exports may include prompts, expected answers, candidate answers, evaluation data, metrics, and costs. Store exports in approved encrypted locations, restrict recipients, and delete temporary downloads when no longer needed. Never commit them to public or shared source repositories.

Configuration exports omit API keys and secret-header values, but still contain operational information such as endpoint URLs, model settings, and test material. Treat them as confidential where appropriate. Before sharing any export, inspect it and apply organization-approved redaction process.

Backup copies need same or stronger protection as live data. Preserve encryption-key material needed for recovery separately and securely; loss of key can make encrypted secrets unrecoverable, while exposure defeats at-rest protection.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

## Operational logging guarantees

Operational logs are designed to record safe diagnostics and lifecycle events without recording sensitive evaluation content. Logging redacts configured secrets and avoids prompts, answers, expected answers, Judge instructions, provider response bodies, API keys, secret-header values, cookies, CSRF tokens, query strings, complete import/export content, exception messages, and tracebacks.

Request diagnostics use route templates rather than concrete request paths. Server generates request IDs for correlation; do not depend on caller-supplied `X-Request-ID` values. Service events use safe metadata such as object identifiers, method names, and counts after successful committed operations.

These safeguards reduce accidental leakage in application logs, but are not authorization or data-loss-prevention system. Keep log files access-controlled and encrypted according to policy. When reporting issue, provide generated request ID, time, non-sensitive action, and status—not copied prompts, outputs, credentials, or full exports.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)
