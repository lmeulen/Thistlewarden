<a id="page-top"></a>

# Troubleshooting

[Top of page](#page-top) · [Manual contents](README.md#manual-top)


### Provider connection failures

Symptoms include failed model connection test, `NETWORK_ERROR`, `TIMEOUT_ERROR`, provider-client/server errors, or run results with error status. Work from lowest-risk checks upward:

1. Confirm application is running locally and endpoint/model profile are active.
2. Check endpoint base URL and completions URL, including required provider path. Do not place API key in URL.
3. Confirm endpoint uses HTTPS for non-loopback provider. Plain HTTP is permitted only for loopback or explicitly acknowledged trusted internal endpoint.
4. Confirm DNS/network route, firewall, proxy policy, and provider availability with operator.
5. Confirm model ID exists for provider and model profile request fields match provider contract.
6. Check endpoint timeout and provider response/answer size limits for workload.
7. Run **Test connection** again after one deliberate change.

Do not paste full endpoint headers, prompts, responses, or credentials into tickets/logs. A saved endpoint does not prove network reachability or provider acceptance. If local browser cannot open application, resolve application startup/readiness first rather than provider configuration.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Authentication and rate-limit errors

Authentication failures usually mean missing, expired, revoked, malformed, or incorrectly placed API key/secret header. Open endpoint, verify authentication method and header name required by provider, then enter replacement secret only when needed. Leaving secret field blank on edit preserves existing value. Confirm endpoint/model are active and test connection. Never expose credential while diagnosing.

Rate-limit errors mean provider rejected or delayed request due to request, token, concurrency, quota, or account limits. Thistlewarden centrally coordinates calls per endpoint and retries retryable failures such as rate limits, timeouts, network failures, and temporary provider-server errors. Persistent errors still become failed jobs/results.

Mitigate rate limits by reducing selected models/cases, lowering run concurrency, waiting for provider reset, checking provider quota, or using approved endpoint with adequate capacity. Do not repeatedly start identical large runs; this can increase cost and backlog. Inspect run error rows and provider account status, then retry failed jobs only after cause is corrected.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Invalid evaluator configuration

Case save rejects invalid evaluator configuration JSON, unknown fields, wrong types, missing required method fields, invalid regular expressions, invalid JSON Schema, duplicate rubric IDs, and invalid numeric/range values. Error message identifies field where available.

Use this recovery sequence:

1. Select intended evaluation method.
2. Start from method-specific example populated in form.
3. Ensure configuration is JSON object with double-quoted property names/strings; no comments or trailing commas.
4. Add only fields documented for selected method.
5. Add required `expected_value`, fragments, schema, pattern, or rubric in evaluator JSON.
6. Use **Test evaluator** for deterministic case with known pass/fail sample.
7. Save case, then inspect stored configuration before run.

Changing method replaces form JSON with its new example, so copy deliberate custom configuration elsewhere before switching. Output preview validates evaluator settings but does not save them. LLM judge output preview is unavailable; use controlled run.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Regular expression and JSON Schema problems

For **Regex**, first decide whether requirement is search anywhere or full candidate match. Set `full_match` for whole answer; otherwise use anchors such as `^` and `$` deliberately. Check escaping: JSON requires backslashes to be escaped, for example `"\\d+"` for digit pattern. Test case sensitivity, multiline, and dot-all behavior with both passing and failing samples. Simplify nested or ambiguous repetition; regex evaluation has short timeout and timed-out pattern fails.

For **JSON Schema**, candidate must be valid standalone JSON. Check quotation, commas, brackets, and that model did not add prose. Enable `allow_markdown_code_fence` only when accepting one complete unlabeled or `json` fenced block. Validate schema against Draft 2020-12 rules, then check required fields, types, enums, nesting, and `additionalProperties`. If optional `expected_value` is configured, parsed JSON must equal it exactly even when schema passes.

Use result reason and metadata in individual run result to identify parse vs schema failure. Reduce complex contract to minimal working schema, verify output tester, then add constraints incrementally.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### LLM judge setup problems

LLM judge needs active judge profile resolved by run override, golden-set override, or active global default. It also needs active judge model profile and active parent endpoint. Check each link before run. Ensure test case selects a registered preset and provides preset-required `expected_value` or `context`.

Judge model must return strict JSON response containing score, passed, reason, and exactly configured preset criterion IDs. Malformed response produces judge parse error. Use capable model, low temperature, adequate output-token limit, and a small pilot suite. Review individual result details to distinguish judge provider failure, parse failure, and ordinary low score.

Do not expect **Test evaluator** to invoke judge: output preview rejects LLM judge because it needs run snapshot/provider call and may cost money. Verify judge through controlled run with non-sensitive data. If judge profile is changed, start new run; existing run uses its snapshot.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Interrupted or incomplete runs

A run may be pending/running while workers process jobs, `completed with errors` when some jobs fail, `failed` when all jobs fail, or `cancelled` after confirmed cancellation. Open run detail and inspect progress, result status, and error rows. Do not delete run before recording/exporting evidence needed for diagnosis.

After application interruption, expired worker leases are recovered when application starts. Run page can show warning that interrupted provider calls may have been repeated and caused duplicate costs. This is possible when provider completed a call before interruption but application could not record completion.

Wait for recovery to settle, then refresh run detail. For terminal run with failures, correct root cause and use **Retry failed jobs** to create new run for failed source profiles. This is a new run using current live configuration and selected failed profiles, not continuation of old immutable snapshot. Cancel only if remaining work should stop; pending jobs are cancelled, while already running provider calls may have begun.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Using request IDs when reporting errors

Application generates request ID for HTTP request correlation and returns it in `X-Request-ID` response header. API error payloads also contain `error.request_id`. Record this ID immediately when page/API action fails.

A useful support report contains request ID, approximate UTC time, action attempted, non-sensitive object type/ID, displayed error code, application version, and whether issue is repeatable. For provider-related failures, include provider request ID from result detail only if organization policy permits it.

Do not include API keys, cookies, CSRF values, secret headers, prompts, expected answers, candidate output, complete provider responses, or unredacted exports. Request IDs help operator correlate safe logs without requiring sensitive payload copies. Caller-provided `X-Request-ID` is not trusted as application correlation value; use ID returned by application.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)
