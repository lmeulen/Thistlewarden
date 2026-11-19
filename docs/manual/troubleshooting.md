<a id="page-top"></a>

# Troubleshooting

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

Start with displayed status and error code. Make one change at time, repeat smallest safe test, and record application request ID. Do not repeatedly launch full runs while configuration is unverified.

### Provider connection failures

Symptoms: **Test model** fails, run result is **Error**, or page shows `NETWORK_ERROR`, `TIMEOUT_ERROR`, provider client/server error.

1. Confirm application is running locally and endpoint plus model profile are active.
2. Verify selected **API format** matches provider protocol.
3. Copy complete **Completions URL** from provider documentation. Do not use website/console URL or put key in URL.
4. Confirm external provider uses HTTPS. Plain HTTP is allowed only for loopback or explicitly trusted internal endpoint.
5. Verify exact model ID and remove unsupported inference/custom parameters.
6. Ask operator to confirm DNS, firewall, route, and provider availability.
7. Check timeout and response/answer size limits.
8. Save, then run **Test model** after one deliberate change.

Endpoint save validates configuration shape only. Model test proves representative call, not every prompt or provider condition.

### Authentication and rate-limit errors

Authentication failure usually means missing, expired, revoked, malformed, or incorrectly placed key/header. Open endpoint and compare API-key header, prefix, and additional headers with provider documentation. On edit, blank existing secret keeps saved value; enter new value only to replace it. Save, then use **Test model**.

Rate-limit errors indicate request, token, concurrency, quota, or account limits. Thistlewarden retries eligible failures within endpoint policy, but persistent failures become error results.

Before retrying:

- reduce run size or concurrency;
- wait for provider reset;
- verify provider quota and service status;
- correct endpoint limits to match approved allowance; and
- avoid starting duplicate large runs, which can increase cost.

Use **Retry failed jobs** only after cause is corrected; it creates new run and new provider calls.

### Invalid evaluator configuration

Save rejects malformed JSON, unknown fields, wrong types, missing required values, invalid regex/schema, or out-of-range numbers.

1. Select intended evaluation method.
2. Start from generated method-specific example.
3. Use JSON object with double-quoted names/strings and no comments/trailing commas.
4. Add only documented fields: required expected value, fragments, pattern, numeric target, schema, or published Judge selection.
5. For deterministic method, use **Test evaluator** with known passing and failing samples.
6. Save and reopen case to confirm stored configuration.

Changing evaluation method replaces current example. Copy needed values before switching. **Test evaluator** does not execute LLM Judge because that requires run context and paid/provider call.

### Regular expression and JSON Schema problems

For **Regex**:

- Decide whether to search within output or require `full_match`.
- Escape JSON backslashes, for example `"\\d+"`.
- Test case sensitivity, multiline, and dot-all settings separately.
- Avoid nested ambiguous repetition; regex timeout produces failed evaluation.

For **JSON Schema**:

- Candidate must be standalone valid JSON.
- Permit Markdown fence only when one complete unlabeled or `json` fence is acceptable.
- Validate against Draft 2020-12.
- Check `required`, types, enums, nesting, and `additionalProperties`.
- Remember optional `expected_value` requires exact parsed JSON equality after schema validation.

Use result reason to distinguish JSON parse failure, schema violation, and expected-value mismatch. Reduce configuration to minimal passing schema, then add constraints incrementally.

### LLM judge setup problems

Verify complete chain:

1. Test case selects exact **published Judge version**; draft cannot execute.
2. Run resolves active **Judge profile** from run override, golden-set override, or global default.
3. Judge profile uses active tested model profile whose endpoint is active.
4. Case provides expected value/context required by selected Judge.
5. Judge profile has adequate output-token limit and either temperature or reasoning override, never both.

Malformed, truncated, oversized, missing, or duplicate final output is error; Thistlewarden does not repair or infer partial score. For **Separate assessment per metric**, failure of any sequential call yields incomplete error and no partial decision.

Use small controlled run with non-sensitive data. Inspect result to distinguish Judge-provider error, response-contract error, and ordinary failed assessment. Profile or Judge changes affect new runs only; completed and started runs retain snapshots.

### Interrupted or incomplete runs

Statuses:

- **Pending/Running** — work still active; aggregates are incomplete.
- **Completed with errors** — some jobs produced no normal evaluation.
- **Failed** — all jobs errored.
- **Cancelled** — confirmed cancellation reached terminal state.

Run page normally refreshes about every two seconds. If it appears stuck, refresh once and check progress/status again. After application restart, expired worker leases are recovered. Provider may have completed interrupted call before application recorded it, so recovered work can repeat and create duplicate cost.

Wait for recovery, inspect error rows, correct cause, then use **Retry failed jobs** if appropriate. Retry creates new run from current active configuration; it does not resume immutable source snapshot. Preserve/export diagnostic evidence before deleting run.

### Using request IDs when reporting errors

Record application request ID shown in error or `X-Request-ID` response header. Useful report contains:

- Thistlewarden version;
- approximate UTC time;
- action attempted;
- displayed error code and application request ID;
- non-sensitive record type/ID; and
- whether issue repeats with synthetic data.

Include provider request ID only when organization policy allows. Never include API keys, cookies, CSRF values, secret headers, prompts, expected answers, candidate output, complete provider responses, databases, backups, or unredacted exports. Use server-generated request ID, not caller-supplied header value.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)
