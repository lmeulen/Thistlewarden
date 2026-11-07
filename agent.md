# Agent guide — Thistlewarden

This file is primary transfer for any new coding agent. Please read this in full before any changes. Then check relevant source code and tests; documentation can lag behind code.

## 1. Opdracht en productgrens

Thistlewarden is a local single-user web app for reproducible evaluation of OpenAI-compatible language models. User manages endpoints, model profiles, golden sets, judge profiles and runs. Runs compare multiple target models on fixed cases, streaming metrics, target costs and quality gates.

Do not change without explicit product decision:

- Dutch GUI and user notifications.
- English code, API fields, logs and technical documentation.
- Python 3.12+, Flask, SQLite, Pydantic and light browser JavaScript.
- Server-rendered HTML/SVG; no heavy frontend stack or external chart dependency.
- One OS host, one application process, local filesystem, bounded threads.
- No authentication: host publishing remains loopback-only.
- Target calls are SSE streaming; judge calls are non-streaming.
- OpenAI-compatible calls are made exclusively server-side.
- Money, prizes, scores, weights and evaluator numbers use `Decimal`, never `float` for domain calculations.

Hoofddocumenten:

- `specification.md`: requirements and invariants.
- `implementation.md`: original 30-step plan.
- `README.md`: installation and user entrance.
- `SECURITY.md`: supported threat boundary.
- `docs/configuration.md`: configuration contracts.
- `docs/operations.md`: lifecycle, backup and recovery.
- `docs/release.md`: release gate and offline E2E.
- `docs/logging-plan.md`: logging policy.
- `reviews/combined_results.md`: Current consolidated review and release blockers.
- `hardening-report.md`: hardening evidence and residual risks.

## 2. Current status

Original MVP functionality has been implemented: six evaluators, provider integration, central limiter, costs, queue/leases/recovery, immutable case/model/judgesnapshots, gates, configuration import/export, run exports, retention, Dutch GUI and security hardening.

**Release is currently no-go.** Compose host validation has been fixed: bootstrap and operational settings allow `HOST=0.0.0.0` only with `CONTAINER_MODE=true`, while Compose publishes externally loopback-only. At a minimum, resolve these remaining confirmed blockers before stable release:

1. **High — migration 9 is not atomic.** `_migration_9()` uses `sqlite3.executescript()`, which implicitly commit and breaks transactional guarantees. See `src/evaluator/migrations.py`.
2. **High — runs are not fully snapshot driven.** Cases, models, judge and gates are snapshotted, but `StreamingTargetCaller` and `JudgeCaller` reload current endpoint configuration and headers. Endpoint change or deactivation after queuing can change run execution.

Important open points afterwards:

- Streaming client translates network/timeout errors into results, allowing to bypass executor retries.
- Run cancellation and shutdown do not abort active provider calls.
- GUI settings may appear saved while constructor-bound runtime components continue to use old values.
- `max_model_answer_chars` is not fully connected; total jobs per run are not sufficiently limited.
- DNS validation has SSRF/DNS-TOCTOU residual risk because connection IP is not pinned.
- Endpoint and secret updates use multiple transactions.
- `/ready` checks SQLite, not worker availability.
- Future schedules are not rejected completely fail-closed.
- CSV output does not neutralize spreadsheet formulas.
- Judge field `prompt_template` is saved and snapshotted, but `build_judge_prompt()` does not use it.
- Model prices are zero placeholders; do not present this as a financial estimate.

Recent full suite: 383 tests green on 2026-11-07 via `venv/Scripts/python.exe -m pytest` (Python 3.12.10, pytest 8.4.2). Ruff check, Ruff format check and strict Mypy are green. Leakscan and Docker E2E were not run.

Environment constraints of current workbook may change, but were most recently:

- Native interpreter: `venv/Scripts/python.exe` (Python 3.12.10, editable install).
- Docker not available in current WSL host; CI includes Dockergate.
- Workbook was not a Git repository. Do not trust `git diff/status` without checking first.

## 3. Non-negotiable invariants

### Runtime en tijd

- Exactly one process. No Gunicorn multiprocess, multiple replicas or network filesystem.
- `python -m evaluator` takes process lifetime `InstanceLock` before initialization.
- Waitress uses one process and four HTTP threads; worker threads follow `RUN_CONCURRENCY`.
- SQLite, WAL, backups and exports must be on the local file system.
- Persistent timestamps and leases: timezone-aware UTC via `evaluator.time`.
- Latency, backoff and waiting times: monotonic clock.
- Queuewrites must remain fenced on lease-owner, generation, expiry and active run status.
- Active provider calls can now not be harshly aborted; timeouts and lease recovery limit damage.

### Configuration

- Priority: `.env > GUI/database > default`.
- `.env` is deliberately loaded with `python-dotenv override=True`; file overwrites processenvironment.
- Environment-controlled settings are read-only in GUI/API.
- Bootstrap settings and operational settings are now two models; prevent further duplication and fix host inconsistency.
- Explicitly classify new settings as bootstrap, restart-required or truly live.

### Secrets en gevoelige data

- `ENCRYPTION_KEY`: URL-safe base64 of exactly 32 random bytes.
- Endpoint secrets: AES-256-GCM, random nonce, record-bound AAD and key versioning.
- Never include secret values ​​in logs, exports, API/frontend responses, snapshots, tests, fixtures or documentation.
- Configuration export does not contain secrets; secret fields are empty/null.
- Prompts, expected answers, model answers and judge details are deliberately plaintext in SQLite and run exports. Treat database, WAL, backups and exports as sensitive.
- Do not read or display local `.env`, provider credentials, or production content unless explicitly and safely requested by user.

### Netwerk en browser

- Provider HTTPS standard required. HTTP only localhost or explicitly trusted internal endpoint with acknowledged warning.
- Provider clients: `follow_redirects=False`, `trust_env=False`, hard response limits.
- One central thread-safe limiter sharing target, judge, discovery, connection test and model test calls.
- Host/Origin validation, CSP nonces and session-bound CSRF remain active.
- `Origin: null` only for GUI transactions with valid session CSRF; never for `/api/v1`.
- App not exposed to LAN/internet; authentication and tenant isolation do not exist.
- No inline JavaScript handlers. Use nonce script and event listeners.

### Logging

- One `StreamHandler` to `stderr`.
- Use `log_event(...)` and allowlisted fields.
- `INFO`: lifecycle, semantic user actions, run progress; no normal GET/polling noise.
- `DEBUG` may show technical metadata, never prompts, answers, expected answers, bodies, SSE chunks, cookies, CSRF, secrets or secret headers.
- Never log, save or export raw provider errors.
- Manual model test diagnostics are response-local, 32 KiB maximum, text/JSON only, HTML escaped and redacted with known header values. Only `ModelProfileTester.test()` uses `diagnostic_mode=True`.

### Costs and usage

- Visible run/model costs and cost gates are target costs only.
- `judge_cost` only audit data remains in storage/export; never add to totals, rankings or gates.
- `total_cost` is strictly unknown if not all target costs are known.
- `known_total_cost` counts known target costs; show known counter separately.
- Targetruntime internally normalizes missing usage components to zero for price calculation, so that fixed request costs continue to count.
- Unreported provider tokens remain in storage `NULL`.
- Generic cost API remains strict with missing usage; do not expand it silently.

## 4. Architectuurkaart

### Composition en lifecycle

- `src/evaluator/server.py`: instance lock, appbouw, workers, Waitress en shutdown.
- `src/evaluator/app.py`: composition root; bouwt repositories/services/clients/queue/workers en registreert blueprints.
- `src/evaluator/config.py`: pre-database bootstrapsettings.
- `src/evaluator/settings.py`: registry, DB/env/default-resolutie en operationele settings.
- `src/evaluator/database.py`: SQLite factory, pragmas en transacties.
- `src/evaluator/migrations.py`: schema v12, backups en migratiemanager.
- `src/evaluator/runtime.py`: process instance lock.

Startupvolgorde: settings laden → instance lock → migreren → services bouwen → verlopen leases zichtbaar maken → workers starten → Waitress starten. Shutdown: HTTP-server sluiten → workers stoppen → limiter sluiten → lock vrijgeven.

### Weblagen

- `src/evaluator/gui.py`: GUI routes and form parsing.
- `src/evaluator/templates/`: server-rendered Jinja.
- `src/evaluator/static/app.css`: layout en responsieve styling.
- `src/evaluator/api/routes.py`: `/api/v1` JSON-routes.
- `src/evaluator/api/openapi.py`: OpenAPI 3.1-contract.
- `src/evaluator/api/handlers.py`: veilige foutmapping.
- `src/evaluator/web_security.py`: host/origin/CSRF/CSP/securityheaders.

GUI and API should use the same services and Pydantic validation. New mutation requires GUI-CSRF; automation-API retains its own origin policy. Update OpenAPI, GUI, config import/export and tests together when field contract changes.

### Domeinen

- `endpoints.py`, `secrets.py`: provider endpoint, headers and encrypted secrets.
- `models.py`: model profiles, inference bindings and prices.
- `golden_sets.py`, `golden_set_io.py`: set/case CRUD, evaluatorconfig, import/export.
- `judges.py`: judge profiles, rubric, prompt contract and strict parser.
- `evaluators/`: deterministic evaluator registry.
- `runs.py`: run creation, snapshots, queue, leases, results and status.
- `workers.py`: claim → heartbeat → target → evaluator/judge → fenced commit → gates.
- `worker_runtime.py`: real target/judge adapters and worker threads.
- `streaming.py`: SSE target client and usage/TTFT/TPS.
- `provider.py`: non-streaming judge client and headers.
- `rate_limits.py`: central limiter and retry executor.
- `costs.py`: exact cost calculation.
- `aggregates.py`, `gates.py`: run/model aggregates and quality gates.
- `run_views.py`, `run_exports.py`: GUI projections and JSON/CSV/HTML export.
- `config_io.py`: secret-free configuration bundle.

### Runflow

1. `RunQueue.create_run()` validates active set/models/judge.
2. Cases, models, judge and gates are snapshotted in one transaction.
3. Only selected target models get jobs; judge-only model can get extra snapshot.
4. Worker claims one job with owner/generation/expiry.
5. Heartbeat extends lease; stale worker is not allowed to commit.
6. Targetcall streams SSE and collects content, latency, TTFT, TPS and usage.
7. Deterministic evaluator or LLM judge produces `EvaluationResult`.
8. Result and metrics are stored fenced.
9. Run status is refreshed; terminal run evaluates gates.

Behoud onderscheid:

- `failed` evaluator result = technically correct execution, content failed.
- `error` result/job `failed` = technical pipeline error.
- `completed_with_errors` = terminal run with some technical errors.

## 5. Evaluatorcontracten

Methoden:

- `exact_match`
- `contains`
- `regex`
- `numeric`
- `json_schema`
- `llm_judge`

Golden-setconfig is validated by Pydantic during write and database read. Regex preserves enforceable timeout. Evaluators return `Decimal` score, boolean `passed`, limited reason, and secure metadata.

### JSON Schema

`JsonSchemaEvaluator`:

1. expect standalone JSON;
2. removes only optional valid Markdownfence when configured;
3. Validates Draft 2020-12;
4. compares full parsed value when `expected_value` is configured;
5. scores binary 0/1: one violation fails the entire case.

Recent model analysis mainly showed valid JSON with contract mismatch, not evaluator crashes. Test set issues include implicit field names/types, language conflicts (`positief` in prompt vs. `positive` as `const`), string vs. array, string vs. integer, and prohibited code fences. When maintaining JSON cases:

- put exact field names, types, arrays and constant values ​​in prompt;
- use `const`/`expected_value` only if exact serialization is required;
- explicitly choose whether code fences are part of test;
- do not change evaluator to artificially pass ambiguous golden cases.

### LLM judge

`build_judge_prompt()` contains explicit output contract, exact criterion IDs/order, prompt injection boundary, and ban on extra text. `parse_judge_output()` is fail closed:

- exactly `score`, `passed`, `reason`, `criteria`;
- each criterion exactly `id`, `score`, `reason`;
- exact criterion set;
- weighted score is recalculated server-side;
- threshold really determines `passed`.

Historical `JudgeParseError` runs were due to a missing output contract and will not be retroactively adjusted. Restart run for new prompt. Judge makes a maximum of two output/parse attempts. Explicitly decide how saved `prompt_template` is applied; currently ignored. Add template injection, escaping, version, and backward compatibility testing before deployment.

## 6. Provider- en modelcontracten

Model parameters are optional; empty means **do not send**. New profiles have `temperature=None`.

`compose_inference_parameters()`:

- protects `model`, `messages`, `stream`, `stream_options` and solid bonds;
- supports configurable `max_tokens_field_path` including nested path;
- supports flat/nested reasoning pad and optional dispatch of `off`;
- refuses conflicts with `custom_parameters`;
- does not allow NaN/infinite JSON.

Model test only uses saved profile over production streaming path with fixed safe prompt. Unsaved form changes should not affect model test.

Targetrequest adds `stream_options.include_usage=true`. Stream is considered complete at `[DONE]` or valid finish reason according to streaming contract. Monitor hard byte/response limits on changes.

Endpoint headers can be plaintext or secretref. Secret values ​​are only resolved server-side. Discovery, target, judge and tests all need to use central limiter.

## 7. Database en migraties

Actueel schema: **v12** (`add_model_parameter_bindings`). Tabellen omvatten settings, secrets, endpoints/headers, models, costs, golden sets/items, judges, runs/snapshots/jobs/attempts/results en gate results.

Migratieregels:

- Only forward migrations; no down migrations.
- Add new migration with next consecutive version.
- Use separate `connection.execute()` statements within existing `EXCLUSIVE` transaction; no `executescript()`.
- Never modify existing historical migration after release can have applied it, except targeted repair strategy with compatibility test.
- New/changed wide `INSERT` uses explicit column names; avoid positional coupling.
- Test empty schema, upgrade from historical version, fault injection/rollback, backup integrity and future-schema failure.
- Existing upgrade creates unique SQLite online backup; fresh database has no pre-upgrade backup.
- Restore always writes to new target path.

SQLite settings (`database.py`) such as WAL, foreign keys, `synchronous=FULL` and busy timeout are part of correctness, not optional tuning.

## 8. Golden sets

Golden-setduplicatie kopieert atomair metadata, cases, volgorde, judgeoverride en quality gates. Bulkdelete valideert UUID's en setlidmaatschap vooraf, verwijdert transactioneel en normaliseert `sort_order`. Historische runrefs blijven beschermd.

## 9. Change Method for Agents

1. Read this guide plus relevant requirements/docs/code/tests in full.
2. Reproduce bug with smallest safe test or read-only database query. Do not show sensitive content.
3. Determine invariants and adjacent contracts before editing.
4. Write regression test that fails before fix where practical.
5. Make smallest coherent change to domain service; don't duplicate logic in GUI/API.
6. Update Pydanticmodel, persistence, migration, config exchange, GUI, API/OpenAPI and tests together if contract changes.
7. Run targeted formatter/ribbon/type/test checks.
8. Run full quality gate for completion.
9. Scan diff/workbook for secrets and payload leaks.
10. Update documentation and this guide when architecture, status, or risk changes.

No broad refactor at the same time as bug fix unless necessary. Maintain transaction boundaries and fencing. Use dependency injection for clock, sleeper, random, transport and repositories in tests. Concurrency tests preferably event/barrier-based, not sleep-based.

For database research:

- first make a backup for changes;
- use parameterized SQL;
- make diagnosis read-only where possible;
- distinguish historical data from current code;
- never modify old run results to simulate new logic.

## 10. Test- en kwaliteitscommando's

Only use the interpreter from `venv/` for all Python commands; don't use global Python. In this Windows/WSL workbook it is `./venv/Scripts/python.exe`. On POSIX this is `./venv/bin/python`.

Windows/WSL-werkmap:

```bash
./venv/Scripts/python.exe -m ruff check .
./venv/Scripts/python.exe -m ruff format --check .
./venv/Scripts/python.exe -m mypy src
./venv/Scripts/python.exe -m pytest -q tests/unit tests/integration
```

POSIX-venv used according to `./venv/bin/python -m ...`.

Gerichte tests volgen modulestructuur, bijvoorbeeld:

```bash
./venv/Scripts/python.exe -m pytest -q tests/unit/test_judges.py
./venv/Scripts/python.exe -m pytest -q tests/unit/test_json_schema_evaluator.py
./venv/Scripts/python.exe -m pytest -q tests/integration/test_worker_pipeline.py
./venv/Scripts/python.exe -m pytest -q tests/integration/test_run_queue.py
./venv/Scripts/python.exe -m pytest -q tests/integration/test_migrations.py
./venv/Scripts/python.exe -m pytest -q tests/integration/test_gui_runs.py
```

After Python change at least: Ruff check, Ruff format check, strict Mypy and relevant tests. For release: full suite plus offline Docker E2E from `docs/release.md`.

Docker release gate when Docker available:

```bash
docker compose -f compose.yaml -f compose.e2e.yaml config --quiet
docker compose -f compose.yaml -f compose.e2e.yaml build --pull
docker compose -f compose.yaml -f compose.e2e.yaml up -d --wait
python scripts/e2e_release.py
docker compose -f compose.yaml -f compose.e2e.yaml logs --no-color > compose.log
! grep -F "release-fixture-secret" compose.log
docker compose -f compose.yaml -f compose.e2e.yaml down -v
```

E2E uses local `mock_provider`, all six evaluators, two models, streaming, judge, usage, costs, gates and exports. Actual Dockerstart is required proof; static testing does not replace this.

Leak scan must exclude at least:

- echte API keys of `.env`-waarden in tracked artefacten;
- secretwaarden in exports/responses/logs/snapshots;
- prompts, candidates, expected answers, requestbodies of SSE-content in logs;
- fixturesecret in E2E-logs.

## 11. Definition of done

Wijziging is klaar wanneer:

- behavior and invariants are explicit;
- regression tests exist for bug/contract;
- GUI/API/config/export paths are consistent where relevant;
- transactions, fencing, Decimal and UTC/monotonic time remain correct;
- security and logging policy has not been weakened;
- Ruff, format, strict Mypy and relevant tests are green;
- entire suite is green for broad change/release;
- Docker E2E is green for deployment/release change;
- do not leak secrets or sensitive payloads;
- docs, OpenAPI and `agent.md` describe current reality.

Always report exactly which checks were run and which could not be run. Do not claim release readiness as long as blockers from §2 are open.
