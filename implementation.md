# Implementatieplan — LLM Evaluator

**Status:** executable plan v1.1
**Source:** `specification.md` v1.0
**Procedure:** small, successive steps; each step ends with automated unit and integration testing and leaves main branch running.

## 1. Uitvoeringsregels

### 1.1 Definition of Done per stap

A step is only complete when:

1. production code and migrations for that step are complete; each new migration has a `N → N+1` fixture with representative existing data and a rollback/break test;
2. said unit tests have been added and pass;
3. said integration tests have been added and pass;
4. existing tests continue to pass;
5. changed API contracts and configuration are documented;
6. No real provider, API key or internet connection is required for testing;
7. no secrets, prompts or answers appear in test logs;
8. pass relevant ribbon, format and type checks.

### 1.2 Testlagen

- **Unit:** pure domain logic, Pydantic validation, serializers, parsers and services with dependencies mocked.
- **Integration:** Flask test client, temporary SQLite database, real repositories/migrations and local mock HTTP provider.
- **GUI integration:** server-rendered HTML via Flask test client, plus browser smoke tests for critical flows.
- **End-to-end:** complete application with mock provider and worker, without external network.

### 1.3 Testconventies

Voorgestelde structuur:

```text
src/evaluator/
  api/
  domain/
  evaluators/
  providers/
  repositories/
  services/
  templates/
  static/
  workers/
tests/
  unit/
  integration/
  e2e/
  fixtures/
```

Tests gebruiken:

- `pytest` and Flask test client;
- temporary SQLite files, not just `:memory:`, for queue and restart tests;
- a local OpenAI-compatible mock server for streaming, retrievals and discovery;
- one `Clock` port with `monotonic()` for latency/backoff and `now_utc()` for persistent leases/retention;
- a dual fake clock of which monotone time and UTC wall clock can be controlled independently;
- deterministic UUID/time factors where snapshots are compared;
- no `sleep` in unit tests.

### 1.4 Ondersteunde MVP-runtime

- One OS host and only a local filesystem volume for SQLite database, WAL and exports; network file systems and shared volumes are not supported.
- One application process contains Flask plus a bounded pool of worker threads. All provider calls — target, judge, connection test and discovery — run through one thread-safe central endpoint limiter in this process.
- Multiple application or worker processes are outside of MVP. Startup validation rejects a configured multiprocess topology. Production server uses exactly one process; Flask development reloader is disabled as soon as workers are active.
- One startup coordinator in the application process performs migrations and recovery before HTTP traffic and worker threads start.
- SQLite uses WAL only on the local volume. Deployment documentation lists supported locking, backup, and volume requirements.
- Worker concurrency is thread concurrency and is limited by operational settings. Central limiter is shared between web and worker threads and closes before database/process shutdown.

### 1.5 Doorlopende verificatie

After every step, at minimum:

```bash
pytest tests/unit tests/integration
```

Zodra tooling bestaat ook:

```bash
ruff check .
ruff format --check .
mypy src
```

Vanaf GUI-stappen:

```bash
npm run lint
npm run typecheck
npm run build
```

If no npm bundler appears to be necessary, replace this with selected TypeScript/JavaScript checks and record command in `README.md`.

## 2. Implementatiefasen en stappen

## Fase A — Fundament

### Stap 1 — Projectskelet en reproduceerbare ontwikkelomgeving

**Goal**

Launchable Flask app with app factory, Python package, test configuration and basic documentation.

**Implementatie**

- Create `.venv` workflow, `requirements.txt`, `.gitignore`, `.env` and `.env.example`.
- Add Flask app factory and `python-dotenv` with `override=True`.
- Add `/health` without database dependency.
- Configure Python logging; Do not use `print` anywhere.
- Add pytest, ribbon, format and type check configuration.
- Add minimal Dutch basic page.
- Validate runtime topology: single process, local SQLite volume and development reloader off on active workers.
- Create `README.md` with install and test commands for PowerShell and POSIX shell.

**Unittests**

- Appconfig chooses defaults when values ​​are missing.
- `.env` overwrites already existing process environment values.
- Invalid booleans/numbers yield startup validation error.
- Loggingconfig does not contain body/prompt fields.
- Multiprocess configuration and an explicit network filesystem volume are denied.

**Integratietests**

- App factory starts in test mode.
- `GET /health` gives `200` and stable JSON contract.
- Basic route renders Dutch page.
- Teststart does not create a production database or export file.

**Acceptatie**

Clean checkout can launch app and tests with documented commands.

---

### Step 2 — Layered configuration and settings

**Goal**

Operational settings support priority `.env > GUI/database > default`.

**Implementatie**

- Define typed settings register with default, data type, range, secret status and env name.
- Implement source resolution and source metadata.
- Include all limits, concurrency, retention, timeouts, host/port and logging from specification.
- Create repository interface for database settings; temporarily use an in-memory adapter until SQLite is available in step 3.
- Ensure environment overrides are read-only for later GUI/API.

**Unittests**

- Each setting uses correct default.
- Database value overrides default.
- Environment value overwrites database value.
- Source is correct: `default`, `database` or `environment`.
- Positive limits and `DEFAULT_RUN_CONCURRENCY <= MAX_RUN_CONCURRENCY` are validated.
- `RUN_RETENTION_DAYS=0` means unlimited.

**Integratietests**

- App starts with minimum `.env`.
- App refuses startup with invalid border combinations.
- Environment value remains leading after attempted database change.

**Acceptatie**

All operational settings have one typed definition and predictable resource priority.

---

### Stap 3 — SQLite-infrastructuur en migraties

**Goal**

Transactional persistent basis with reproducible schema initialization.

**Implementatie**

- Add SQLite connection factory with foreign keys, busy timeout and appropriate WAL settings.
- Choose and configure a transactional migration mechanism with incremental schema history.
- Create tables for settings and schedule history.
- Serialize migrations with an SQLite lock; second startup waits bounded or fails safely.
- Create a consistent local SQLite backup and document restore before each schema upgrade.
- Implement transaction/repository helpers.
- Add CLI commands for database initialization/migration and backup/restore.

**Unittests**

- Transaction commit on success and rollback on exception.
- Repository maps database values ​​to domain types.
- UTC timestamp helpers produce timezone-aware values.

**Integratietests**

- Empty database migrates to latest version.
- Migration is idempotent.
- Foreign-key violation fails.
- Database setting is retained after restart.
- Baseline `0 → 1` creates expected schedule; every subsequent `N → N+1` fixture retains representative existing data and constraints.
- A forced error mid-migration leaves schema and data on version `N` intact.
- Two simultaneous app starts are serialized by a SQLite migration lock; only one performs each migration.
- Pre-migration backup can be restored to a new local database file and migrated again.
- Two test app instances can access the database in a controlled manner.

**Acceptatie**

Application can start from empty file, migrate and save settings persistently.

---

### Stap 4 — Uniform API-foutcontract, request-ID en OpenAPI-basis

**Goal**

All API routes provide safe, consistent errors and are documented.

**Implementatie**

- Add `/api/v1` blueprint.
- Implement error types and mapping to HTTP states.
- Generate request ID per request and return it in error response/header.
- Add safe exception handler without stack trace in response.
- Launch OpenAPI document with health and settings schemas.

**Unittests**

- Each domain error type maps to correct code/status.
- Validation errors contain field details.
- Sanitizer removes registered secrets from error text.

**Integratietests**

- Unknown API route gives consistent JSON error contract.
- Forced internal error does not leak stack trace or body.
- Each error contains request ID.
- OpenAPI document is syntactically valid and contains implemented routes.

**Acceptatie**

From now on, new routes can only be added via shared error and OpenAPI contract.

## Fase B — Security en kernconfiguratie

### Stap 5 — Secretopslag, redactie en veilige logging

**Goal**

API-keys en geheime headers versleuteld opslaan; gevoelige inhoud nooit loggen.

**Implementatie**

- Add encryption service based on `ENCRYPTION_KEY`.
- Create a generic `secret_records` table, migration and repository; endpoint/header references follow in step 6.
- Support key version so that later rotation remains possible.
- Add secretredactor for logging and provider notifications.
- Introduce structured log context with run, case and execution/test ID.
- Ban request/response body logging in HTTP client and Flask.

**Unittests**

- Encrypt/decrypt round trip.
- Same plaintext produces different ciphertext due to nonce.
- Wrong key and corrupted ciphertext fail safely.
- Editor removes full and prefixed secret value.
- Logformatter records correlation IDs, not prompts/answers.

**Integratietests**

- Secretrepository round-trip stores ciphertext, key version and nonce; plaintext is never in SQLite.
- Simulated provider exception with secret/body doesn't leak anything to captured logs.
- Repository update with empty secret keeps existing value.

**Acceptatie**

Automated leaktest searches all database fields, responses and logs for fixturesecret available in this step and finds nothing.

---

### Stap 6 — Endpointdomein, validatie en CRUD-API

**Goal**

Endpoints, authheader, prefix, extra headers en rate-limitconfiguratie persistent beheren.

**Implementatie**

- Create endpoint and header models plus migration; point secret values to `secret_records` from step 5.
- Validate unique name, header names, timeout, retries, RPM, TPM and concurrency.
- Implement HTTPS/localhost/trusted-internal-HTTP rules.
- Implement CRUD service and API routes.
- Mask secrets when reading; prepare hard delete blocking for historical references.

**Unittests**

- Valid/invalid HTTP header names.
- HTTPS, localhost HTTP, loopback HTTP and explicitly trusted internal HTTP.
- Untrusted external HTTP is rejected.
- Negative/invalid rate limits are rejected; empty means unlimited.
- API key prefix is ​​correctly combined without unwanted trim.
- Secret fields serialize only masked.

**Integratietests**

- Endpoint CRUD with regular and secret extra headers.
- Unique name constraint.
- Patch without key retains key; patch with key replaces it.
- Database contains only references and encrypted secrets; API responses never contain plaintext.
- Empty secretpatch retains existing value; new value replaces via secretrepository.
- Redirect configuration is not allowed as an implicit host/protocol change.
- OpenAPI contains endpoint contracts.

**Acceptatie**

Endpoint can be created and read back completely via API without secret leak.

---

### Stap 7 — Modeltestprofielen en inferenceparameters

**Goal**

Manage multiple profiles for the same model ID with temperature, reasoning and provider parameters.

**Implementatie**

- Create model profile and pricing models plus migration.
- Implement unique display name per endpoint.
- Build request parameter composer with optional temperature, top-p, max tokens and custom JSON.
- Support configurable reasoning field, including nested path, default/custom value and `off` behavior.
- Add CRUD API.

**Unittests**

- Two profiles may have the same model ID.
- Duplicate display name within endpoint fails.
- Nested reasoning path builds object correctly.
- `off` is sent or dropped according to profile.
- Unsupported parameters are not sent as `null`.
- Custom parameters cannot unintentionally overwrite protected core fields.

**Integratietests**

- Model profile CRUD under endpoint.
- Cascade/archive behavior upon endpoint deactivation.
- API round-trip preserves Decimal prices and inference parameters.
- Invalid partial high context price configuration is rejected.

**Acceptatie**

The same provider model can be configured with multiple unique test profiles.

---

### Stap 8 — Kostenengine

**Goal**

Calculate all EUR costs deterministically and without double counting.

**Implementatie**

- Create usage and pricing value objects with `Decimal`.
- Implement standard vs. high context rate on `X`/`X+1`.
- Process cached input and reasoning as subsets.
- Process fixed request costs and unknown usage.
- Save calculation specification/result suitable for audit snapshot.

**Unittests**

- Basic input/output costs.
- Exact `X` uses standard rate; `X+1` high rate.
- Cached/reasoning tokens are not counted twice.
- Missing special rate is corrected.
- Unknown subset usage gives agreed `unknown` status.
- Subset greater than total is invalid.
- Fixed costs for successful/paid calls and not for unpaid errors.
- Decimal precision and storage to at least eight decimal places.

**Integratietests**

- Profile from database plus provider usage provides expected cost record.
- Stored profile prices plus explicitly supplied pricing-value objects remain immutable during one calculation; historical run snapshot coverage follows in step 19.
- API serializes amounts as precise decimal strings/numbers without float artifacts.

**Acceptatie**

Cost fixtures from specifications are exactly reproducible.

## Fase C — Golden sets en evaluatoren

### Stap 9 — Golden sets en testcase-CRUD

**Goal**

Golden sets en testcases inclusief evaluatorconfiguratie persistent beheren.

**Implementatie**

- Create golden set and item tables with UUID, `case_id`, prompts, expected answer, method, config, category, tags, weight, enabled and sorting.
- Define Pydantic-discriminated union for evaluator methods.
- Enforce input limits and method-dependent required fields.
- Add CRUD and pagination API.

**Unittests**

- Unique `case_id` within set.
- Weight must be greater than zero.
- Prompt/system/expected response limits.
- `expected_answer` required for exact match.
- One optional system prompt and exactly one user prompt.
- Each evaluator configuration chooses the correct scheme.

**Integratietests**

- Golden set and item CRUD.
- Transactional sort change.
- Pagination and filtering by category/tag/enabled.
- Deleting/archiving respects references.
- Database JSON-config is revalidated on read.

**Acceptatie**

Complete test case from specification can be saved and read losslessly via API.

---

### Stap 10 — Exact-match- en contains-evaluatoren

**Goal**

Eerste deterministische evaluatoren inzetbaar maken.

**Implementatie**

- Define shared evaluator interface and results contract.
- Implement normalization in established order.
- Implement `exact_match` and `contains` with `all`/`any`.
- Store normalized values ​​and match metadata.

**Unittests**

- CRLF/CR to LF.
- Trim, casefold and collapse whitespace separately and combined.
- Order of normalization.
- Exact match pass/fail.
- Contains `all`/`any`, empty fragments, overlap and Unicode.
- Result includes score `0/1`, passed and safe reason.

**Integratietests**

- Evaluator registry chooses method from saved test case.
- API validation rejects empty contains fragments.
- Evaluator output serializes and deserializes losslessly via the shared result contract; database persistence follows in steps 19/20.

**Acceptatie**

Fixturecases leveren exact verwachte scores en redenen.

---

### Stap 11 — Regex- en numeric-evaluatoren

**Goal**

Patroon- en numerieke controles toevoegen.

**Implementatie**

- Implement regex search/full match and flags.
- Use regex engine/execution with enforceable timeout.
- Implement Decimal parser for semicolon, optional thousand separator and extraction mode.
- Implement absolute and relative tolerance.

**Unittests**

- Regex search/full match and every flag.
- Invalid pattern is rejected before run.
- Catastrophic pattern stops within testable timeout.
- Numeric entire-answer/first-number.
- Period/comma and thousand separator.
- Absolute, relative and combined tolerance on limit values.
- None/ambiguous value indicates fail, no system error.

**Integratietests**

- Saved regex/numericconfig validates during run preparation.
- Regex/numericoutput round-trips via the shared result contract; database persistence follows in steps 19/20.
- Timeout of one regex case does not affect other cases.

**Acceptatie**

Regex- en numerieke fixturematrix slaagt volledig.

---

### Stap 12 — JSON Schema-evaluator

**Goal**

JSON-antwoorden tegen Draft 2020-12 en optionele expected value beoordelen.

**Implementatie**

- Add JSON Schema validator.
- Validate schedule on save/import/run preparation.
- Defensively unpack optional single Markdown code defense.
- Implement optional deep JSON similarity.
- Provide compact, bounded validation reason.

**Unittests**

- Valid/invalid schedule.
- Valid/invalid candidate JSON.
- Codefence on/off and surrounding text.
- Nested object, arrays, required and types.
- Deep equality independent of object key order.
- Boundary of error detail.

**Integratietests**

- Invalid schema blocks test case storage/import.
- Valid schema evaluates from database config.
- Model content is saved escaped prepared when HTML is displayed.

**Acceptatie**

Draft 2020-12 fixtures en codefencecases leveren verwachte uitkomst.

---

### Stap 13 — Golden-set JSON/CSV-import en -export

**Goal**

Golden sets atomair en verliesvrij uitwisselen.

**Implementatie**

- Implement canonical JSON format.
- Implement CSV parser/serializer with JSON fields for configs/tags.
- Add file and item limits.
- Provide preview/validation errors with row and case ID.
- Make imports atomic.

**Unittests**

- JSON and CSV escaping, multiline prompts and Unicode.
- Booleans, Decimal weight, tags and evaluator config.
- Invalid row reports correct location.
- Too large file/set is rejected before writing.
- Export order is stable.

**Integratietests**

- JSON round-trip preserves all fields.
- CSV round-trip retains all supported fields.
- One invalid row results in zero imports.
- Duplicate `case_id` is clearly reported.
- Download headers and file names are correct.

**Acceptatie**

Sample set can be exported, deleted and replaced identically via both formats.

## Fase D — Providerintegratie

### Step 14 — OpenAI request builder and non-streaming client

**Goal**

Veilige gedeelde HTTP-basis en judge-compatible non-streaming completioncall.

**Implementatie**

- Build provider client with timeouts, no cross-host/protocol redirects and secure headers.
- Compose messages, model and inference parameters.
- Parse `choices[0].message.content`, usage and request ID.
- Enforce response size limit.
- Classify HTTP, network, timeout and parse errors.

**Unittests**

- Requestbody for system+user and model profile.
- API key prefix and custom headers, including env secret reference.
- Redirect policy.
- Response parser for valid/missing/malformed contract.
- Error classification 401/403/429/4xx/5xx/timeout/network.
- Body limit.

**Integratietests**

- Local mock provider receives exact headers/body.
- Secret headers do not appear in logs.
- Redirect to other host/protocol is not followed.
- Provider errors are translated to API error category.

**Acceptatie**

Non-streaming mock completion works completely without an external network.

---

### Stap 15 — SSE-streaming, TTFT en TPS

**Goal**

Targetantwoorden streamen en prestatiemetrics betrouwbaar meten.

**Implementatie**

- Add `stream=true` and `stream_options.include_usage=true`.
- Build incremental SSE parser with fragmented rules/events.
- Assemble `delta.content`, final usage and `[DONE]`.
- Measure total latency, TTFT and generation duration with monotonic clock.
- Calculate TPS only with known output tokens and positive duration.
- Save partial text if stream is broken/too large; mark error and don't evaluate.

**Unittests**

- One and multiple chunks; bytes split in the middle of UTF-8/event.
- Empty role-delta does not count as a first token.
- Final usage before/without choices.
- `[DONE]`, provider end indication and unexpected EOF.
- TTFT/latency with fake clock.
- TPS normal, unknown usage and zero duration.
- Stream/reply size limit.

**Integratietests**

- Mock-SSE provider provides curated response and metrics.
- Broken stream saves partial answer, but doesn't start evaluator.
- Final cached/reasoning usage reaches cost engine.
- Connection test reports secure status and TTFT.

**Acceptatie**

Streamingfixture produceert exact antwoord, tokenusage, latency, TTFT en TPS.

---

### Stap 16 — Retrybeleid en centrale endpoint-rate limiter

**Goal**

Centrally manage provider traffic within the supported single-process model for RPM, TPM and concurrency.

**Implementatie**

- Implement retry decider with exponential backoff and jitter via `Clock.monotonic()`.
- Respect `Retry-After` in seconds and HTTP date form; converts HTTP dates via `Clock.now_utc()` to monotone wait durations without persistent monotone timestamps.
- At appstartup, create exactly one thread-safe `CentralEndpointLimiter` and inject the same instance into target, judge, connection test and discovery clients.
- Track shared RPM, TPM, and concurrency state across all web and worker threads per endpoint.
- Reserve RPM/concurrency exactly and TPM conservatively; reconcile usage and release concurrency in `finally` on success, error or cancellation.
- Expose an in-memory observe event/queue reason `rate_limited_wait`; persistent job status follows in step 19/20.
- Close limiter checked so that waiting calls cancel before workers and database stop.

**Unittests**

- Retry only timeout/network/429/5xx; not other 4xx.
- Backoff sequence and jitter with seeded random and monotone fake clock.
- `Retry-After` parsing with independently controlled UTC and monotone fake clock.
- RPM window and concurrency acquire/release from multiple threads.
- TPM reservation, reconciliation with real usage and unknown usage.
- Error and cancellation while waiting/call leaks no concurrency permit or TPM reservation.
- Shutdown wakes up all waiting callers with a controlled cancellation.

**Integratietests**

- Mockprovider 429→success delivers two calls and correct observer events; persistent attempts follow in step 20.
- Parallel calls do not exceed endpoint concurrency.
- Target, non-streaming judge and connection test calls demonstrably share the same limiter instance; discovery is added in step 17.
- Simultaneous target and judge calls share RPM/TPM budget and cannot pass each other.
- Appstartup rejects more than one process; Limiter behavior therefore does not have to be cross-process.

**Acceptatie**

Deterministic dual-clock testing proves that no provider call is sent before available central capacity and that all call types within one process share the same endpoint budgets.

---

### Stap 17 — Model discovery

**Goal**

Manage models manually or selectively via OpenAI-compatible `/models`.

**Implementatie**

- Implement secure URL suggestion.
- Add authenticated `GET` to models URL via the same `CentralEndpointLimiter` as all other provider calls.
- Parse `data[].id` and validate response size.
- Add preview and selective import; never overwrite an existing profile.

**Unittests**

- URL derivation for `/chat/completions`, `/completions` and non-derivable URL.
- Deduplication/validation of `data[].id`.
- Name conflict generates proposal without changing existing profile.
- Discovery error does not contain responsebody/secret.

**Integratietests**

- Mock `/models` receives the same auth/custom headers and shares limiter budget with a concurrent target call.
- Selective import creates only selected profiles.
- Existing model ID can be re-imported with unique profile name.
- Error response does not change database.

**Acceptatie**

Discoveryflow importeert geselecteerde mockmodellen en behoudt bestaande profielen exact.

## Fase E — Judge, runs en queue

### Stap 18 — Judgeprofielen, rubric en outputparser

**Goal**

Judge configuration and deterministic processing of criterion subresults.

**Implementatie**

- Create judge profile/template/rubric models and migration.
- Implement hierarchy globally → golden set → runoverride.
- Build prompt with safely separated prompt, expected answer, rubric, context and candidate.
- Parse JSON, optionally from one code fence.
- Validate exact criterion set, subscores and reasons.
- Calculate weighted total score and threshold; save judge value as audit control.

**Unittests**

- Standard five criteria and equal weights.
- Add/replace rubric and positive weights.
- Missing/duplicate/unknown criteria are rejected.
- Weighted score and inclusive threshold `0.7`.
- Judge score deviation `>0.01` is highlighted.
- Prompt injection text remains quoted data.
- Code defense and malformed JSON.

**Integratietests**

- CRUD judge profile and override resolution.
- Mockjudgeoutput parses to the shared result contract with calculated score and reasons; result storage follows in step 19/20.
- Invalid output causes one retry via the service and then `judge_parse_error`.
- Judge costs/latency remain separate from target metrics in the contract.

**Acceptatie**

Vaste judgefixture levert reproduceerbare subresultaten, totaalscore en pass/fail.

---

### Stap 19 — Runsnapshots en duurzame jobqueue

**Goal**

Create a run atomically with immutable snapshots and one job per case/model combination.

**Implementatie**

- Create run, case snapshot, model snapshot, job, result and attempt tables with migration evolution fixture.
- Create snapshot service without secrets; snapshot prices and all evaluator/judge contracts from steps 8 and 10–12/18.
- Validate active cases/profiles, limits and judge requirements before start.
- Create jobs transactionally.
- Model lease fields `lease_owner`, strictly ascending `lease_generation` and timezone-aware UTC `lease_expires_at`; never persistent monotonic time.
- Implement atomic claim that sets owner, increases generation and determines expiry via `Clock.now_utc()`.
- Implement heartbeat as conditional compare-and-set update on job ID + owner + generation; heartbeat only extends a valid lease.
- Require the same fencing token for every attempt, status, result, and job completion write. Stale writes modify zero rows and return an explicit `LeaseLost` result.
- Enforce unique outcome constraint as a second layer of defense, not as a replacement for fencing.

**Unittests**

- Job number = enabled cases × selected profiles.
- Snapshot contains all required config/prices but no secrets.
- State transitions are valid; invalid transition fails.
- Lease claim/expiry via independently controlled UTC fake clock; monotonic clock change does not affect lease.
- Every new claim increases `lease_generation`, even with the same owner.
- Heartbeat and all conditional writes only pass current owner + generation + unexpired lease.
- Stale worker gets `LeaseLost` and cannot write attempt, status, result or completion.
- Run limits and empty selection.

**Integratietests**

- Run creation writes snapshots/jobs in one transaction.
- Error during snapshot does not leave half run.
- Configuration change after start does not change snapshot.
- Two worker threads cannot claim the same job at the same time.
- Long call with periodic heartbeat is not taken over by the second worker thread.
- After UTC expiry, second worker with higher generation claims; late heartbeat/commit from first worker is completely rejected.
- Unique result constraint prevents duplicate local results as an additional invariant.
- Migration from previous schema version preserves Decimal prices, timezone-aware timestamps, evaluator/judge JSON, and existing ciphertext references.

**Acceptatie**

Run is fully reproducible from snapshots; queue claims and all subsequent writes are fenced by owner plus generation.

---

### Stap 20 — Workerpipeline en runstatus

**Goal**

Jobs uitvoeren: targetcall → evaluator/judge → kosten → resultaat → aggregatiestatus.

**Implementatie**

- Build worker loop with dependency injection and bounded thread pool within the single application process.
- Connect streaming client, central limiter, retries, evaluator registry and judge.
- After claim, start a heartbeat loop with an interval well shorter than the lease duration; stop provider/pipeline work as soon as possible after `LeaseLost`.
- Only save every request attempt and secure error metadata with current owner + generation.
- Commit evaluator/judge result, costs and job completion in one conditional transaction with the same fencing token.
- Calculate run status and progress.
- Implement cancellation and rerun failures as a new run.

**Unittests**

- Pipeline sequence and evaluator selection.
- Partial stream is not evaluated.
- One job error does not affect the next job.
- Run status completed/completed_with_errors/failed/cancelled.
- Cancellation skips pending jobs; active call may be completed.
- Attempt/retry registration.
- Heartbeats stop on completion, cancellation, `LeaseLost` and shutdown.
- Every pipeline write carries exactly the same owner/generation token.

**Integratietests**

- Completes run with multiple cases, models and deterministic evaluators.
- Run with LLM judge completes and splits target/judge costs.
- Mixed provider errors yield `completed_with_errors`.
- Cancelroute stops unstarted jobs.
- Rerun-failures only contains failed combinations as new snapshots/jobs.
- Forced lease takeover during blocked provider call: old worker cannot commit attempt/result/status afterwards; new worker completes one local result.

**Acceptatie**

Full headless run against mock provider ends with correct results and status.

---

### Stap 21 — Restart-recovery en leaseherstel

**Goal**

Actieve runs na procesrestart veilig hervatten.

**Implementatie**

- Perform startup recovery before workers start.
- Only make expired leases eligible again via UTC; increase generation only with new claim and retain old owner/generation for diagnosis.
- Mark resumed jobs `recovered_after_interruption` via the new valid fencing token.
- Retain completed/finally failed jobs.
- Add warning about possible duplicate provider charges.

**Unittests**

- Only lease expired via `Clock.now_utc()` will become available again; monotonic reset/restart does not change this.
- Unexpired lease with missing heartbeat remains shielded until expiry.
- Completed job remains untouched.
- Run status is restored correctly.
- Recovery logging contains IDs and numbers, not content.

**Integratietests**

- Stop worker after claim, move UTC fake clock past expiry, start new app and complete job with higher generation.
- Already completed result will not be called again.
- Old worker resumes after recovery but all its late writes are rejected by fencing.
- Result plus job commit crash scenario does not cause duplicate local result.
- Recovery marker and warning are visible via API.

**Acceptatie**

Gesimuleerde restart hervat uitsluitend onafgeronde combinaties.

## Fase F — Metrics, gates en uitwisseling

### Stap 22 — Aggregaties en rangschikking

**Goal**

Per model/run alle kwaliteits-, timing-, token- en kostenmetrics berekenen.

**Implementatie**

- Implement weighted score, pass/error rate and counts.
- Implement mean, median and p95 for latency, TTFT and TPS.
- Sum token classes and target/judge fees.
- Calculate costs per evaluated/passed case.
- Rank score → error rate → cost.
- Show unknown explicitly and correctly exclude it from aggregations.

**Unittests**

- Weights, errors and empty populations.
- Median/p95 for small and larger sets with fixed percentile method.
- Unknown TTFT/TPS/fees.
- Cost per passed case at zero passes.
- Deterministic tie-break ranking.

**Integratietests**

- Result fixtures in SQLite provide expected model aggregates.
- API filters change result list, unsaved total figures.
- Snapshot prices drive historical cost aggregation.

**Acceptatie**

Golden aggregate fixture produceert exact verwachte tabel en rangorde.

---

### Stap 23 — Quality gates

**Goal**

Evaluating golden-set defaults and run overrides by gate outcome.

**Implementatie**

- Definieer gateschema en persistente defaults/overrides.
- Evalueer per-modelgates en totale runkostengate.
- Implementeer `not_evaluated`, `passed`, `failed`, `indeterminate`.
- Bewaar per gate gemeten waarde, grens en uitkomst.

**Unittests**

- Includes min/max limits.
- Any supported gate.
- One failing model fails run-gate.
- Unknown required metric returns `indeterminate`.
- No gates and non-terminal/canceled run give `not_evaluated`.
- Runoverride replaces defaults completely according to contract.

**Integratietests**

- Terminale run evalueert gates automatisch.
- API toont gate-uitkomsten per model.
- Totale kostengate bevat target plus judge.
- Resultaat blijft stabiel na wijziging golden-setdefaults door snapshot.

**Acceptatie**

Gatefixture dekt alle vier uitkomsten.

---

### Step 24 — Full configuration JSON import/export

**Goal**

Lossless exchange of functional configuration without secrets.

**Implementatie**

- Define versioned Pydantic schema `1.0`.
- Export endpoints/headers, profiles/prices, judge profiles, golden sets and gates.
- Always export secret value `null`, possibly with env-var reference.
- Implement preview, conflict map, `merge` and `replace`.
- Archive historically used records when replacing.
- Perform import atomically.

**Unittests**

- Serializer contains all functional fields.
- No ciphertext/plaintextsecret in JSON.
- Schema/reference/UUID conflicts.
- Keep stable UUID vs remapping.
- Merge and replace scheduler without database effects.

**Integratietests**

- Full export→empty database→import round-trip.
- Missing secrets are marked and block provider calls until filled in.
- Merge doesn't overwrite anything outside of preview plan.
- Replace archives used records and preserves history.
- One validation error rolls back entire import.

**Acceptatie**

Semantic comparison before export and after import is the same, except for deliberately deleted secrets.

---

### Stap 25 — Resultaatexports en retentie

**Goal**

Export runs as JSON/CSV/offline HTML and safely clean up old runs.

**Implementatie**

- Build full JSON export and flat CSV export.
- Build standalone HTML report with inline CSS, print CSS and strict CSP.
- Escape all provider content.
- Add sensitive content warning.
- Implement manual run deletion and periodic retention cleanup.

**Unittests**

- CSV quoting, Unicode and stable columns.
- HTML escaping of script/markup content.
- CSP and no external URL/assets/scripts.
- Filename sanitizing.
- Retention selection uses `Clock.now_utc()` and UTC end time; active runs excluded; `0` unlimited; monotonic time is never used persistently.

**Integratietests**

- JSON/CSV/HTML contain metrics, snapshots and subresults, not secrets.
- Export ignores active UI filters and includes full run.
- HTML opens without network dependencies and contains print CSS.
- Manual delete and cleanup remove dependent records atomically.
- Cleanup rollback on forced error preserves entire run.

**Acceptatie**

Three exports from one fixture run are usable, complete and secret-free.

## Fase G — GUI

### Step 26 — GUI for settings, endpoints and models

**Goal**

Manage all operational settings, endpoints, headers and model profiles via Dutch GUI.

**Implementatie**

- Create shared layout, navigation, central Dutch UI texts and CSRF.
- Build settings screen with source badge and read-only environment overrides.
- Build endpoint list/form with password field, headers, trusted-HTTP warning, rate limits, test and discovery.
- Build model profile form with inference parameters, reasoning and complete pricing configuration.
- Add accessible validation and confirmation notifications.

**Unittests**

- Viewmodel masks secrets and shows correct source badge.
- Formmapping keeps empty secret as “do not change”.
- Dutch text catalog contains used keys.
- Price/reasoning form to domain model and back.

**Integratietests**

- Render Flask HTML routes without secrets.
- CSRF blocks invalid mutations.
- Create/change endpoint plus custom headers via GUI.
- Environmentoverride is visible but not editable.
- Discovery selection creates profile; connection test shows safe outcome.

**GUI-rooktests**

- Keyboard flow through endpoint and model form.
- Labels, focus and error link present.
- Desktop layout works at 1024 px.

**Acceptatie**

User configures an endpoint with headers and two model profiles without an API client.

---

### Step 27 — GUI for golden sets, judge and configuration exchange

**Goal**

Manage test cases, evaluators, judge and complete JSON configuration via GUI.

**Implementatie**

- Build golden set list, item editor and dynamic evaluator fields.
- Build JSON/CSV import preview and export actions.
- Build judge profile/rubric editor with weights and template.
- Build configuration-JSON import/export with merge/replace-preview and missing-secretsstatus.
- Show active limits in forms.

**Unittests**

- Form/view model per evaluator type.
- Rubric weight and criterion validation.
- Import preview presentation of row/field errors and conflicts.
- Secret status never shows secret value.

**Integratietests**

- Each evaluator method can be saved via GUI.
- Golden-set JSON/CSV import works via multipart upload.
- Judge hierarchy is displayed correctly.
- Configuration export/download and import preview/commit work.
- Replace requires explicit confirmation.

**GUI-rooktests**

- Create golden set with at least one test case per evaluator.
- Import configuration with missing secret and then fill in secret via GUI.
- All forms are keyboard operable.

**Acceptatie**

Full functional configuration can be managed without manual database/API operation.

---

### Step 28 — GUI for runs, progress and results

**Goal**

Runs starten, volgen, annuleren, vergelijken en exporteren.

**Implementatie**

- Build dashboard, new run wizard and run history.
- Show size, profiles, judge override, concurrency and quality gates before start.
- Poll run status maximum every two seconds.
- Build results comparison, sorting, filters and item detail.
- Show latency, TTFT, TPS, tokens, costs, judge sub-results, errors and gate outcomes.
- Add cancel, rerun failures, delete and exports with confirmation/warning.

**Unittests**

- Viewmodels for unknown metrics, monetary amounts and local time.
- Sort/filter parameter parsing.
- Progress calculation.
- Status and gate badges.

**Integratietests**

- Starting run via GUI creates correct snapshots/jobs.
- Pollendpoint shows increasing progress.
- Result filters and item detail show correct data without secrets.
- Cancel/rerun/delete require CSRF and confirmation flow.
- Export alert and downloads work.

**GUI-rooktests**

- Start mock run with two models and multiple evaluators.
- Track to completion, sort by score/TTFT/TPS/cost and open detail.
- Cancel second run and rerun failures.

**Acceptatie**

Primary user flow from specification is fully executable via GUI.

## Fase H — Oplevering en hardening

### Stap 29 — Performance, security en robuustheid ✅

**Goal**

Demonstrably achieving non-functional limits.

**Implementatie**

- Add pagination/indexes and targeted aggregate queries.
- Harden CSP, CSRF, security headers, upload and response limits.
- Control SSRF, redirect, secret editing and HTML escaping centrally.
- Optimize SQLite write patterns and busy handling.
- Add controlled shutdown of workers and leases.

**Unittests**

- Securityheaderconfig.
- URL/redirect edge cases: IPv4/IPv6 loopback, user info, hostname variants.
- Upload/stream limits on exact border and border+1.
- Shutdown status transitions.

**Integratietests**

- 10,000 result fixtures: paginated list and aggregates within agreed test budget on reference profile.
- 20 model profiles/run and configurable concurrency.
- SQLite busy/retry scenario without data loss.
- XSS fixtures remain text in GUI and HTML export.
- Automated repository/log/response scan does not find fixturesecrets.

**Acceptatie**

Security- en performancetestverslag documenteert gemeten resultaten en eventuele hardwarecontext.

**Status:** completed on 2026-11-06. Evidence and residual risks are in `hardening-report.md`; release gate: 348 tests, Ruff lint/format and strict Mypy passed.

---

### Stap 30 — Docker, documentatie en volledige end-to-endacceptatie ✅

**Completed on 2026-11-06.** Production Waitress entry point, non-root multi-stage image,
Compose-local volume/healthcheck, fully pinned runtime constraints, CI release gates,
offline mock provider, version-bound release fixture and native offline E2E have been added.
Docker/Compose were not installed on the output host; real image build/start/health
therefore required by `.github/workflows/ci.yml` and can only be used locally on a Docker host
confirmed. Static container contract tests and full native production pipeline E2E pass.

**Goal**

Reproduceerbare oplevering vanaf lege checkout.

**Implementatie**

- Create production-ready `Dockerfile` and `docker-compose.yml` with exactly one application process and bounded web/worker threads; no Flask reloader.
- Add one Docker-local persistent volume for SQLite/WAL/exports, local port binding and health check; document that bind mounts are only supported when they can be proven to use a local filesystem.
- Have container entry point, as the sole startup coordinator, first perform backup/migrations and recovery and only then start the HTTP server and worker threads.
- Finalize `.env.example`, local `.env`, README and OpenAPI.
- Add sample golden-set and mockprovider.
- Document Windows/POSIX, Docker, configuration sources, JSON schema, security, backup and limitations.

**Unittests**

- Validate config schema and JSON examples against application models.
- Documentation code blocks/config examples are machine tested where possible.

**Integratietests**

- Dockerimage builds.
- Container starts with exactly one application process, health check becomes healthy and local database volume survives restart.
- Composeconfig does not contain a network filesystem/shared volume and does not publish a second app or worker replica.
- OpenAPI contains and validates all actual `/api/v1` routes.
- Migration from empty database and upgrade from previous release fixture with existing data working in container; backup can be restored and migrated again.

**End-to-endacceptatietest**

1. Launch application and mock provider via Docker Compose.
2. Import functional configuration JSON without secrets.
3. Enter secret via GUI.
4. Discover two mock models and create multiple temperature/reasoning profiles.
5. Import golden set with all six evaluation methods.
6. Start run with judge and quality gates.
7. Check live progress, streaming metrics, costs and rankings.
8. Restart application during second run and check queue recovery.
9. Export JSON, CSV and HTML; scan for secrets.
10. Check retention/delete flow and audit metadata.

**Acceptatie**

Full test suite and end-to-end adoption succeed from clean checkout without external network.

## 3. Traceabilitymatrix

| Specification area | Implementation steps |
|---|---|
| Configuration and `.env` priority | 1–3, 24, 26 |
| Security, secrets, logging, SSRF | 4–6, 14, 24–26, 29 |
| Endpoints, headers and discovery | 6, 14, 16, 17, 26 |
| Model profiles, reasoning and prices | 7, 8, 26 |
| Golden sets and test cases | 9, 13, 27 |
| Deterministic Evaluators | 10–12 |
| LLM judge and subresults | 14, 18, 20 |
| Streaming, TTFT and TPS | 15, 20, 22 |
| Rate limiting and retries | 16, 17, 20 |
| Runtime topology, local storage and central limiter | 1, 3, 16, 20, 30 |
| Runs, snapshots, fencing and sustainable queue | 19–21 |
| Migration evolution and restore | 3, all schema steps, 30 |
| Metrics and Ranking | 8, 15, 22 |
| Quality gates | 23, 28 |
| Configuration JSON | 24, 27 |
| Result exports and retention | 25, 28 |
| Full GUI | 26–28 |
| Performance and hardening | 29 |
| Docker, docs and E2E | 30 |

## 4. Mijlpalen

### M1 — Configureerbare kern

Na stap 8:

- app, database and settings work;
- endpoints/headers and model profiles exist;
- secrets are safe;
- cost engine has been tested.

### M2 — Headless evaluatiecomponenten

Na stap 18:

- alle evaluatoren werken;
- golden sets importeren/exporteren;
- providerstreaming, discovery, limiter en judge werken afzonderlijk.

### M3 — Volledige headless evaluator

Na stap 25:

- runs, queue, recovery, metrics, gates, configuration exchange and exports work via API;
- core can be fully automated tested before GUI building.

### M4 — Functionele GUI

Na stap 28:

- all primary functions are available via Dutch GUI;
- endpoints and headers are fully manageable via GUI.

### M5 — MVP-release

Na stap 30:

- hardening, Docker, documentation and end-to-end acceptance completed;
- application complies with `specification.md`.

## 5. Releasegate

MVP may only be delivered when:

```bash
pytest
ruff check .
ruff format --check .
mypy src
# plus gekozen frontend lint/typecheck/build
# plus Docker build en end-to-endacceptatietest
```

alle slagen en daarnaast:

- no test has been skipped without a stated reason;
- no real API key in repository, logs, fixtures or artifacts;
- OpenAPI and database migrations are up to date;
- README exactly matches actual start and test commands;
- configuration and result exports pass automatic secret scan;
- all traceability rows are demonstrably ready.
