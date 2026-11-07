# Specificatie — LLM Evaluator

**Status:** final v1.0
**User interface language:** Dutch; code, API contracts, logs and technical documentation are in English
**Purpose of this document:** complete, implementable product specification for building, testing and delivering a local web application that reproducibly evaluates multiple LLM models on a golden set.

> This release contains final MVP product and implementation decisions and provides the basis for development, testing, build, and adoption.

## 1. Purpose and success criteria

The application allows a user to:

1. manage one or more configurable LLM endpoints;
2. configure one or more models per endpoint;
3. manage golden sets of prompts, expected answers and evaluation method;
4. Run selected models against the same golden set;
5. automatically assess answers with at least `exact_match` and `llm_judge`;
6. compare quality, latency, token usage and costs per model and run;
7. save, inspect and export results.

The MVP is successful when a user can configure two models on different endpoints, run one golden set, track progress, and view and export a reproducible comparison report without any code changes.

## 2. Scope

### 2.1 MVP

- Local single-user web application.
- CRUD via GUI for endpoints, headers, models, judge profiles, golden sets, evaluator configuration and quality gates.
- JSON import/export for all functional configuration, including endpoints and headers, but without secret values.
- Manual import and import/export of golden-set items.
- Evaluation methods:
  - exact match (`exact_match`);
  - fragment present (`contains`);
  - regular expression (`regex`);
  - numerical comparison with tolerance (`numeric`);
  - JSON Schema Validation (`json_schema`);
  - LLM as a judge (`llm_judge`).
- Batch run on chosen golden set and models.
- Persistent storage of configuration, runs, raw answers, scores and errors.
- Dashboard with aggregated and item level results.
- Cost calculation based on configurable prices and actual reported tokens.
- JSON, CSV and standalone HTML report of results.
- Secure management of API keys.
- Automatic tests and local installation instructions.

### 2.2 Buiten MVP

- Multi-user accounts, roles and SSO.
- Public cloud hosting or distributed workers.
- Fine-tuning or model training.
- Human review workflow with multiple reviewers.
- Semantic similarity as a separate evaluator.
- Statistical significance tests.
- CI integration or command-line quality gates.
- Streaming judge answers; target model responses do use streaming for performance metrics.
- Tool/function calling, images and other multimodal input.

These points can be added later without fundamentally breaking any data model or API.

## 3. Gebruikersrol en hoofdflows

### 3.1 Rol

**Evaluator Manager:** configures everything, starts runs and views/exports results. MVP is a local single-user application without authentication.

### 3.2 Hoofdflow: configureren

1. User creates endpoint with name, completions URL, API key and API key header name.
2. Application optionally tests the connection.
3. User adds models with model ID, display name, parameters and token prices.
4. User may mark one model as a judge model.

### 3.3 Hoofdflow: golden set maken

1. User creates golden set with name, description and version.
2. User manually adds items or imports file.
3. User chooses evaluation method and associated settings for each item.
4. Application validates set before execution.

### 3.4 Hoofdflow: evaluatierun

1. User chooses golden set and one or more target models.
2. User checks estimated size: number of items × number of models.
3. User starts run.
4. Application freezes snapshot of relevant configuration.
5. Application performs model calls, stores response metadata, and evaluates each response.
6. UI shows progress, successes and errors.
7. Afterwards, application shows ranking and detail results.

## 4. Functionele requirements

### FR-1 Endpointbeheer

Per endpoint is stored:

- unique UUID;
- unique name;
- completions URL, including full path;
- optional models URL for model discovery;
- API key as secret;
- configurable header name in which API key is placed, for example `Ocp-Apim-Subscription-Key`;
- configurable API key prefix, empty by default, for example `Bearer ` including space;
- zero or more additional static headers as key/value pairs, for example tenant ID, project ID or provider-specific headers;
- a secret status per extra header; secret values ​​are stored encrypted, shown masked and never logged or exported;
- timeout in seconds, default 60;
- maximum number of retries, default 2;
- optional request per minute (RPM) limit;
- optional tokens per minute (TPM) limit;
- optional limit for concurrent requests;
- active/inactive status;
- created/updated timestamps.

Gedrag:

- Backend does all external LLM calls; browser never receives API keys.
- URL must be HTTPS by default.
- HTTP is only allowed for `localhost` and loopback addresses without additional confirmation.
- A remote internal HTTP endpoint can only be saved when the user explicitly marks the endpoint as trusted internal and acknowledges a security warning.
- Redirects to another protocol or host are not automatically followed.
- Header name is validated as a valid HTTP header name.
- API key prefix is ​​free text with a short maximum length; empty prefix only sends key, for example for `Ocp-Apim-Subscription-Key`, and `Bearer ` supports Authorization authentication.
- Endpoint, API key header, API key prefix and all additional headers can be created and changed via GUI.
- API key is never fully returned by API or UI; input via GUI uses a password field and existing value is only shown masked.
- Empty API key during update retains existing value.
- Delete is rejected if historical data is dependent; deactivation remains possible.
- Empty rate limit fields mean that application does not enforce a limit for that dimension.
- Rate limits apply jointly to all target and judge calls to the same endpoint.
- An endpoint limiter reserves capacity before shipment. RPM and concurrency are precisely monitored by the application. TPM uses provider usage from completed responses plus a conservative estimate for new calls; Missing usage is visibly highlighted and can make TPM enforcement less precise.
- Pending calls remain visible in run state as `rate_limited_wait` and do not fail as long as run is not canceled.
- HTTP 429 uses provider header `Retry-After` if present, otherwise exponential backoff with jitter.
- Model discovery does an authenticated `GET` to models URL with the same API key header and additional custom headers as completion calls.
- If models-URL is empty, UI suggests a URL by finally replacing `/chat/completions` or `/completions` in completions-URL with `/models`; user confirms or changes it before use. No reliable derivation means no automatic URL.
- Expected discovery contract is OpenAI-compatible: model IDs are in `data[].id`.
- User selects which found models are imported as a model test profile. Each new profile is initially given model ID as display name and requires checking parameters and prices.
- Existing profiles are never automatically changed or overwritten. Pre-existing model ID may be re-imported with a unique profile name.
- Discovery errors do not change any configuration and show safe status without logging secrets or response body.
- Manually adding models is always possible.
- Connection test sends a minimum streaming completion to a chosen model, validates protocol and reports reachability, TTFT and safe error category without logging response content.

### FR-2 Modelbeheer

A model belongs to exactly one endpoint and contains:

- UUID;
- endpoint ID;
- model ID as expected by external API;
- display name;
- optional description;
- test profile with inference parameters:
  - optional `temperature`; standard `0` for models that support temperature;
  - optional `reasoning` setting for models that support reasoning:
    - configurable request field name, for example `reasoning`, `reasoning_effort` or a nested path such as `reasoning.effort`;
    - predefined values ​​`off`, `low`, `medium` and `high`;
    - free custom value for provider-specific variants;
    - with `off` it is configurable per profile whether the value is sent literally or the field is omitted completely;
  - optional `max_tokens`;
  - optional `top_p`;
  - extensible JSON object for other provider-specific parameters;
- it can be explicitly set per parameter that it is not sent to the endpoint;
- price configuration, always in EUR:
  - standard price per 1 million input tokens;
  - standard price per 1 million output tokens;
  - optional price per 1 million cached input tokens;
  - optional price per 1 million reasoning tokens;
  - optional fixed costs per successful request;
  - optional context threshold `X` in input tokens;
  - if threshold active: high-context price per 1 million input tokens;
  - if threshold active: high-context price per 1 million output tokens;
  - optional high context rate for cached input tokens and reasoning tokens;
- active/inactive status.

Multiple model configurations may use the same external `model-ID` so that temperature and reasoning variants can be tested against each other. Each configuration has a unique display name within the endpoint and serves as a separate test profile. Results show both external model ID and profile name.

### FR-3 Extern completion-protocol

MVP ondersteunt OpenAI-compatible `POST` chat completions. Targetmodelcalls gebruiken streaming:

```json
{
  "model": "<model-id>",
  "messages": [
    {"role": "system", "content": "<optional-system-prompt>"},
    {"role": "user", "content": "<prompt>"}
  ],
  "temperature": 0,
  "reasoning": "<configureerbare waarde, alleen indien ondersteund>",
  "stream": true,
  "stream_options": {"include_usage": true}
}
```

Targetrespons is OpenAI-compatible Server-Sent Events (SSE):

- content fragments from `choices[0].delta.content` are merged in order;
- stream ends at `data: [DONE]` or valid provider end indication;
- usage is read from final event if available: `usage.prompt_tokens`, `usage.completion_tokens`, `usage.total_tokens`;
- optional provider request ID comes from response header.

Application processes stream incrementally on backend. Browser does not need to render tokens live; UI does show active status. A broken stream saves received partial text for diagnosis, marks case as error, and does not evaluate partial text. Judge calls remain non-streaming and use `choices[0].message.content` for reliable JSON output contract.

`temperature` and `reasoning` are included from the selected model test profile. Unsupported parameters are omitted, not sent as `null`. Reasoning supports a configurable request field name, including nested field path, the default values ​​`off`, `low`, `medium`, `high` and a free custom value.

Apart from the configurable reasoning field and other explicit provider parameters, request fields and response paths remain fixed according to this OpenAI-compatible contract. Response and usage paths are not configurable in MVP.

Unknown additional fields are ignored. Missing or invalid required fields will produce a clear item error without crashing the entire run.

### FR-4 Golden-setbeheer

Golden set:

- UUID;
- unique name;
- Description;
- version label as free text, default `1.0`;
- optional tags;
- created/updated timestamps.

Golden-set-item:

- UUID;
- golden-set-id;
- stable external key `case_id`, unique within set;
- prompt, obligatory;
- optional system prompt;
- expected answer, mandatory for `exact_match`, optional for `llm_judge`;
- evaluation method;
- evaluator configuration;
- optional category and tags;
- optional weight, standard `1.0`, larger than `0`;
- enabled status;
- fixed sorting order.

MVP supports text prompts with one optional system message and exactly one user message. Multi-turn calls are excluded from MVP. Data model and internal service boundary must allow subsequent multi-turn messages without fundamental migration of runs and results.

### FR-5 Import en export golden sets

Ondersteunde formaten:

- JSON for lossless round-trip;
- CSV for easy bulk editing.

Canoniek JSON-formaat:

```json
{
  "name": "Voorbeeldset",
  "description": "...",
  "version": "1.0",
  "tags": ["support"],
  "items": [
    {
      "case_id": "case-001",
      "prompt": "Wat is 2 + 2?",
      "system_prompt": null,
      "expected_answer": "4",
      "evaluation_method": "exact_match",
      "evaluator_config": {
        "trim": true,
        "case_sensitive": true,
        "normalize_newlines": true
      },
      "category": "rekenen",
      "tags": ["basis"],
      "weight": 1.0,
      "enabled": true
    }
  ]
}
```

CSV-kolommen:

`case_id,prompt,system_prompt,expected_answer,evaluation_method,evaluator_config,category,tags,weight,enabled`

JSON objects/lists in CSV are stored as JSON string. Import shows validation errors with row/case ID and imports nothing when one or more rows are invalid (atomic).

### FR-6 Exact-match-evaluator

Configuration per item:

- `trim`: remove leading/trailing whitespace, default `true`;
- `case_sensitive`: default `true`;
- `normalize_newlines`: convert CRLF/CR to LF, default `true`;
- `collapse_whitespace`: default `false`.

Normalization is carried out in a fixed order: normalize newlines, trim, collapse whitespace if necessary, normalize case if necessary. Then full string comparison.

Output:

- `score`: `1.0` if equal, otherwise `0.0`;
- `passed`: `score == 1.0`;
- short reason with yes/no match, without secret data;
- normalized expected and actual values ​​are kept in result metadata for explainability;

### FR-7 Aanvullende deterministische evaluatoren

All deterministic evaluators return `score` (`0.0` or `1.0`), `passed`, a short reason, and method-specific metadata. A configuration error is reported before a run by golden set validation and not counted as a model failure.

#### `contains`

Checks whether one or more expected text fragments appear in the complete candidate answer.

Configuration:

- `expected_fragments`: non-empty list of strings;
- `match_mode`: `all` or `any`, default `all`;
- `case_sensitive`: default `true`;
- `normalize_newlines`: default `true`;
- `collapse_whitespace`: default `false`.

`passed = true` when according to `match_mode` all or at least one normalized fragment occurs. Empty fragments are invalid.

#### `regex`

Checks candidate response with a regular expression.

Configuration:

- `pattern`: required, not empty;
- `full_match`: default `false`; `false` is searched for, full answer must match `true`;
- `case_sensitive`: default `true`;
- `multiline`: default `false`;
- `dot_all`: default `false`.

Application compiles pattern during validation. Regex execution is safely timed out to limit catastrophic backtracking. Invalid pattern blocks run start.

#### `numeric`

Extracts numerical value from candidate answer and compares it with expected number.

Configuration:

- `expected_value`: mandatory decimal number;
- `absolute_tolerance`: optional non-negative decimal number;
- `relative_tolerance_percent`: optional non-negative decimal percentage;
- `extraction`: `entire_answer` or `first_number`, default `entire_answer`;
- `decimal_separator`: `.` or `,`, default `.`;
- `allow_thousands_separator`: default `false`.

At least one tolerance is specified; without tolerance, exactly numerically equal. For both tolerances, the answer is successful if the difference falls within at least one tolerance. Calculation uses decimals, not binary floats. No or more ambiguous values ​​at `entire_answer` yield `passed = false` for a reason, not a system error.

#### `json_schema`

Parses candidate response as JSON and validates against configurable JSON Schema.

Configuration:

- `schema`: required valid JSON Schema object;
- `allow_markdown_code_fence`: default `true`;
- `schema_draft`: standard Draft 2020-12;
- optional `expected_value` for additional deep equality, absent by default.

Schema itself is checked in golden set validation. Candidate with invalid JSON or schema anomalies gets `score = 0.0` and compact validation reason. Codefence may be removed defensively; other surrounding text is not allowed.

### FR-8 LLM-as-a-judge-evaluator

#### Configuration

Judge configuration follows fixed hierarchy:

1. one global standard judging profile;
2. optional different judge profile per golden set;
3. an optional override of the judge profile per run;
4. no model override per item: only rubric, threshold and context.

A judge profile contains:

- judge endpoint and judge model test profile;
- judge-inference parameters, defaults to `temperature = 0`;
- judge prompt template with version;
- default pass threshold, `0.7`;
- standard rubric with five criteria and equal weights:
  1. `factual_correctness`: factual accuracy;
  2. `completeness`: completeness;
  3. `relevance`: relevance;
  4. `instruction_compliance`: compliance with explicit instructions;
  5. `consistency`: no contradictions with expected answer or rubric;
- maximum one retry in case of invalid judge output.

Per item:

- optional expected answer;
- optional item rubric that supplements or completely replaces standard criteria; each criterion has stable `id`, description and positive weight;
- if the expected answer is missing, the rubric must contain sufficient independent assessment criteria;
- optional item-specific pass threshold;
- optional extra context;
- no choice for another judge model.

Target model may also be a judge. UI warns of possible self-judging bias when target model and judge model are the same.

#### Judge-input

Judge ontvangt ondubbelzinnig gescheiden:

- originele prompt;
- verwacht antwoord, indien aanwezig;
- rubric/criteria;
- kandidaatantwoord.

Template instructs judge to ignore prompt injections in candidate response and treat them solely as data.

#### Verplicht judge-outputcontract

```json
{
  "score": 0.82,
  "passed": true,
  "reason": "Korte feitelijke totaaltoelichting",
  "criteria": [
    {
      "id": "factual_correctness",
      "score": 0.9,
      "reason": "Feiten komen overeen met referentie."
    },
    {
      "id": "completeness",
      "score": 0.7,
      "reason": "Eén ondersteunend detail ontbreekt."
    }
  ]
}
```

- Judge returns exactly one subresult for each applicable rubric criterion and no unknown criterion IDs.
- Each criterion score is a number between `0.0` and `1.0` and has a short reason.
- Application calculates leading total score itself as weighted average: `sum(criteriumscore × gewicht) / sum(gewicht)`.
- `score` returned by judge is stored for checking but is not leading. Deviation greater than `0.01` will be flagged in audit metadata without rejecting results.
- Application determines `passed` itself from the calculated total score and threshold; `passed` returned by judge is read for checking but is not leading.
- Subscores, subreasons, weights, calculated total score and raw judge output are saved and shown in item detail and exports.
- JSON in Markdown code defense may be unpacked defensively. Missing, duplicate or unknown criteria and other invalid output are requested again once and then end up as an evaluation error.
- Judge call token usage, latency and costs are stored separately and not mixed with target model costs.
- Raw judge output and prompt template version are saved for auditability.

### FR-9 Runconfiguratie en uitvoering

Run bevat:

- name, automatic proposal with date/time, manually changeable;
- golden set;
- one or more target model test profiles;
- optional override of the judge profile inherited from the golden set or global standard;
- optional run note;
- concurrency, globally configurable, default 4;
- snapshots of golden-set items, model settings, prices, endpoint URL/header name (never secret), evaluator configuration, judge template and quality gates set.

Statussen:

`pending`, `running`, `completed`, `completed_with_errors`, `failed`, `cancelled`.

Item/model-uitvoering:

1. target completion aanroepen;
2. antwoord en metadata opslaan;
3. gekozen evaluator uitvoeren;
4. resultaat opslaan;
5. aggregaten bijwerken.

Eisen:

- Execution is done via a durable SQLite job queue so that page refresh and application restart do not lose a run.
- Each case/model combination is a separate persistent job with status, attempt number, lease owner, and lease expiration time.
- Worker claims jobs atomically with a time-bound lease; Expired leases are made available again after startup.
- After server restart, runs with status `running` are automatically resumed. Case/model combinations that have already been successfully completed or have definitively failed will not be re-executed; only `pending`, pending and suspended jobs continue.
- An external call that was active during the crash cannot be reliably resumed or canceled at the provider. If the lease expires, that job will be executed again. This can cause duplicate provider costs; gets result `recovered_after_interruption` and UI/log shows warning with execution/test ID.
- Result persistence status and job completion are done in one database transaction to avoid local double counting.
- Run can be canceled; active HTTP call is allowed to complete, calls that have not yet started are skipped.
- Retry only on timeouts, network errors, HTTP 429 and HTTP 5xx, with exponential backoff and jitter.
- No retry on other HTTP 4xx.
- Every attempt and final error category is recorded.
- Startup performs queue recovery before workers start and records number of resumed runs and jobs without logging prompt or response content.
- Responses are not cached: each run measures new completions.

### FR-10 Metrics en aggregatie

#### Per modelantwoord

- score `0.0–1.0`;
- passed/failed/error;
- target total latency in milliseconds, measured from just before request transmission to complete received stream;
- time to first token (`TTFT`) in milliseconds, measured from just before request submission to first non-empty content delta;
- generation duration in seconds, from first non-empty content delta to complete received stream;
- tokens per second (`TPS`) = output tokens / generation duration;
- input, output and total tokens, if provider reports;
- target costs;
- judge latency, tokens and costs if applicable;
- error type and error message;
- number of attempts.

#### Per model within run

- number of total, completed, passed, failed and error;
- pass rate = `passed / (passed + failed)`; errors stand apart;
- average weighted score = `sum(score × weight) / sum(weight)` over successfully evaluated cases;
- error rate = `errors / totaal`;
- mean, median, and p95 target latency;
- mean, median, and p95 TTFT;
- mean, median, and p95 TPS;
- as input-, output-, cached-input- and reasoningtokens;
- total target costs;
- total judge fees;
- total run costs = target costs + allocated judge costs;
- cost per successfully reviewed case;
- cost per passed case.

Results always show sample size and error count to avoid misleading comparisons. Ranking is based on highest weighted score, then lowest error rate, then lowest total costs. TTFT or TPS is `unknown` when no content delta, output token usage, or valid positive generation duration is available; application does not estimate token numbers. For very short generation where first and last chunk coincide, TPS is `unknown` to avoid division by zero and misleading values.

#### Kostenformule

All costs are in EUR only. Each model test profile has standard input and output prices and can also contain optional rates for cached input tokens, reasoning tokens and fixed costs per successful request. Optionally, the profile has one context threshold `X`, determined by the number of input/prompt tokens of the request:

- `input_tokens <= X`: use standard input and output prices;
- `input_tokens > X`: use high-context input and output prices for the entire request.

The threshold is therefore not graduated: when context is greater than `X`, all input and output tokens of that request are calculated at the high-context rate. Equality falls under the standard rate.

Cached input tokens are a subset of input tokens. Reasoning tokens are a subset of output tokens. They are never double taxed:

```text
regular_input_tokens  = input_tokens  - cached_input_tokens
regular_output_tokens = output_tokens - reasoning_tokens

selected_prices = high_context_prices if input_tokens > context_threshold
                  else standard_prices

input_cost     = regular_input_tokens  / 1_000_000 × selected_input_price
cached_cost    = cached_input_tokens   / 1_000_000 × selected_cached_price
output_cost    = regular_output_tokens / 1_000_000 × selected_output_price
reasoning_cost = reasoning_tokens      / 1_000_000 × selected_reasoning_price
total_cost     = input_cost + cached_cost + output_cost + reasoning_cost
                 + fixed_cost_per_successful_request
```

Regels:

- Without a separate cached rate, cached tokens are charged at the chosen regular input price.
- Without a separate reasoning rate, reasoning tokens are calculated at the chosen regular output price.
- When context threshold is active, high-context input and output prices are mandatory. High-context cached and reasoning rates are optional; if they are missing, each falls back to a regular high-context input or output rate, respectively.
- Fixed request costs are charged once for each provider call with a valid completion response, including target and judge calls. Failed HTTP/parse attempts do not incur fixed request costs, unless the provider reports usage anyway; then provider usage and fixed request costs are registered.
- Retries with a valid completion response are registered as separate paid calls.
- Application reads cached and reasoning usage from OpenAI-compatible detail fields if present, including `usage.prompt_tokens_details.cached_tokens` and `usage.completion_tokens_details.reasoning_tokens`. Missing detail fields mean `unknown`, not automatically zero, unless provider explicitly reports zero.
- If subtotal token fields are known, application validates that subsets are not greater than totals; invalid usage creates charge status `unknown`.

Context threshold and both high-context base prices are together optional and validated as one configuration. A partially completed graduated scale is invalid. All prices must be non-negative. Calculation uses decimals, not binary floats. Storage with at least 8 decimal places; display adaptively rounded. If required usage is missing, cost status is `unknown`; application does not guess tokens or fees.

### FR-11 Resultateninterface

Runoverzicht toont:

- status and progress;
- start/end time and duration;
- golden set version and models;
- comparative table with core metrics;
- sort by score, pass rate, latency, TTFT, TPS and costs;
- filters by model, method, category, tag, pass/fail/error;
- item detail with prompt, expected answer, candidate answer, score, reason, total latency, TTFT, TPS, tokens, cost and error details;
- indication when metric is unknown.

Charts are useful but not required for MVP. Tables must contain all required information.

### FR-12 Configuration import and export

All functional configuration can be managed via GUI and exported/imported losslessly as one version-bound JSON file:

- endpoints, API key header, API key prefix, models URL, rate limits and non-secret additional headers;
- secret header definitions and optional environment variable references, but never secret values;
- model test profiles, inference parameters and EUR prices;
- golden sets, items and evaluator configuration;
- judge profiles, templates and rubrics;
- quality gate defaults.

Canoniek topniveau:

```json
{
  "schema_version": "1.0",
  "exported_at": "2026-01-01T12:00:00Z",
  "endpoints": [],
  "model_profiles": [],
  "judge_profiles": [],
  "golden_sets": []
}
```

Secret fields are exported as follows:

```json
{
  "header_name": "Authorization",
  "prefix": "Bearer ",
  "secret": true,
  "value": null,
  "env_var": "PROVIDER_API_KEY"
}
```

- `value` of a secret is always `null`; without `env_var` user must enter secret after import via GUI.
- Non-secret custom headers may contain their values ​​in JSON.
- Import validates `schema_version`, references, unique names, full configuration and limits before writing.
- UI shows preview with counts, warnings, missing secrets and conflicts.
- User selects `merge` or `replace`. `replace` requires explicit confirmation and does not delete historical runs; used records are archived.
- Import is atomic. A validation error changes nothing.
- Imported objects keep stable UUIDs where safe; conflicts are mapped to new UUIDs according to preview.
- Configuration export is separate from result and golden set exports.

Operational application settings can be set via settings GUI and `.env`. `.env` is leading: an existing environment value overwrites the database value and is shown in the GUI as a read-only override with source name. Without environment value, GUI/database value applies; if this is also missing, the documented default applies. Secrets can be provided via GUI or environment reference, but are never included in regular JSON export.

### FR-13 Resultatenexport

- JSON export includes run metadata, snapshots, all item results, raw responses, metrics and errors; secrets excluded.
- CSV export contains one row per case/model combination and flat core fields.
- Standalone HTML report includes inline styling, run metadata, model comparison, aggregates and item results. It requires no external assets, scripts, API calls or network connection and remains readable offline after export.
- HTML has print CSS so user can create a PDF via browser. Native PDF generation is outside of MVP.
- Sensitive model content is correctly HTML escaped; Content Security Policy prevents execution of injected content.
- File name contains run name and ISO date.
- Export does not follow active UI filters and always exports full run.

### FR-14 Quality gates

Quality gates are optionally configurable per run and can be saved as defaults in a golden set. Runoverride replaces golden-set defaults.

Ondersteunde gates:

- minimale gewogen gemiddelde score;
- minimale pass rate;
- maximaal foutpercentage;
- maximale gemiddelde total latency;
- maximale p95 total latency;
- maximale p95 TTFT;
- minimale gemiddelde TPS;
- maximale totale kosten in EUR.

Score, pass rate, error, latency, TTFT and TPS gates are assessed per selected target model profile: each profile must reach each set gate. Maximum total costs apply as standard for the entire run, including target and judge costs. UI shows the measured value, limit and outcome per gate and model.

In addition to execution status, run has a gate outcome:

- `not_evaluated`: no gates set or run not yet terminal;
- `passed`: all set gates have been reached;
- `failed`: at least one gate was not reached;
- `indeterminate`: required metric is `unknown`, for example missing usage for costs/TPS.

By default, `indeterminate` counts as a failure for automation and is shown separately from `failed`. Gates are only finally evaluated at `completed` or `completed_with_errors`; a canceled or technically failed run gets `not_evaluated`. Decimals and percentages are validated exactly. Limit includes: minimum pass at `waarde >= grens`, maximum at `waarde <= grens`.

### FR-15 Historie en beheer

- Runs are searchable chronologically by name, golden set and model.
- Historical run remains interpretable after current configuration changes due to snapshots.
- Run delete requires confirmation and atomically deletes dependent item results.
- Automatic retention is configurable via `RUN_RETENTION_DAYS`; empty or `0` means unlimited storage and is default.
- With positive retention, periodic cleanup only removes completed, failed, and canceled runs whose end time falls outside of retention, including dependent snapshots and results, in a transaction.
- UI shows scheduled due date of runs when retention is active.
- Results export shows a warning before download that file may contain sensitive prompts and answers.
- Golden sets, endpoints and models with historical references are deactivated or archived, not hard deleted.

## 5. Non-functional requirements

### NFR-1 Architectuur en stack

Conform `agent.md`:

- backend: Python 3.12+, Flask, Pydantic;
- database: SQLite, including sustainable job queue and worker leases;
- frontend: HTML, CSS and TypeScript/JavaScript;
- LLM traffic exclusively server-side via OpenAI-compatible endpoints;
- pytest for backend testing.

Implementation uses server-rendered Flask UI with light TypeScript/JavaScript interactivity and REST-JSON endpoints. No heavy frontend framework. Backend structure separates routes, services, evaluators, provider client, repositories, and domain models.

### NFR-2 Deployment and Configuration

Ondersteunde startmethoden:

1. primary: Docker Compose;
2. alternative: directly via Python `.venv` on Windows and Linux.

Docker Compose:

- builds application from repository;
- publishes only configurable local host port;
- mount persistent volumes for SQLite data and exports;
- reads configuration and secrets from local `.env`;
- contains health check;
- does not require an external database or job queue for MVP.

Direct launch uses the same configuration, database, and application code. `README.md` documents commands separately for PowerShell and a POSIX shell.

Configuration:

`.env` is loaded with `python-dotenv` and `override=True`. Project provides both `.env` and `.env.example`; real secrets are only in local `.env`, which is in `.gitignore`.

Application configuration includes at least:

```dotenv
FLASK_ENV=development
SECRET_KEY=change-me
DATABASE_PATH=./data/evaluator.db
HOST=127.0.0.1
PORT=5000
DEFAULT_REQUEST_TIMEOUT_SECONDS=60
DEFAULT_MAX_RETRIES=2
RUN_CONCURRENCY=4
LOG_LEVEL=INFO
RUN_RETENTION_DAYS=0
MAX_PROMPT_CHARS=1000000
MAX_SYSTEM_PROMPT_CHARS=100000
MAX_EXPECTED_ANSWER_CHARS=1000000
MAX_MODEL_ANSWER_CHARS=10000000
MAX_JUDGE_RUBRIC_CONTEXT_CHARS=100000
MAX_IMPORT_FILE_BYTES=104857600
MAX_PROVIDER_RESPONSE_BYTES=104857600
ENCRYPTION_KEY=change-me
```

Functional configuration is managed via GUI/database and can be imported/exported via JSON. This includes endpoints and headers. Operational settings are managed via settings GUI/database and `.env`; `.env` values ​​take precedence and their active source is visible in GUI. API keys are set via GUI or environment reference and encrypted at rest with `ENCRYPTION_KEY`; key itself is only in `.env`.

### NFR-3 Security

- Never log, export or send secrets to the frontend.
- API keys and custom headers marked as secret are encrypted in SQLite.
- Prompts, expected answers, model answers, judge input and judge output are stored unencrypted in local SQLite.
- Prompt and response content is never included in application, access, or error logs.
- Where necessary, logs refer exclusively to UUIDs such as run ID, case ID and a unique execution/test ID.
- External error responses are cleared before logging; responsebody is not logged by default because it can repeat input or model content.
- Log messages and external error texts are edited for known secret values.
- External response body is not injected as HTML; UI escapes all model content.
- CSRF protection on mutating browser requests.
- Input lengths and response sizes are limited with defaults adjustable via `.env`:
  - prompt: 1,000,000 characters;
  - system prompt: 100,000 characters;
  - expected answer: 1,000,000 characters;
  - model answer: 10,000,000 characters;
  - judge rubric plus context: 100,000 characters;
  - import file: 100 MiB (`104857600` bytes);
  - provider response or full stream: 100 MiB (`104857600` bytes).
- Limits are enforced before processing. An exceedance stops in a controlled manner with validation or provider response error; partial oversized streams are not evaluated. Startup validates that all limits are positive integers.
- Server-side URL validation reduces SSRF risk. HTTPS is required by default; HTTP is only allowed for localhost/loopback or an internal endpoint explicitly marked as trusted. Redirects to other hosts/protocols are not automatically followed.
- Dependencies are recorded in `requirements.txt`.

### NFR-4 Betrouwbaarheid

- SQLite foreign keys enabled.
- Transactional transactions and imports.
- HTTP calls always have a timeout.
- Run error in one case does not affect other cases.
- Database supports consistent schema migrations.
- Application shows useful error message; stacktrace only in development log.

### NFR-5 Performance

MVP defaults are a maximum of 10,000 items per golden set, 20 model test profiles per run, 4 concurrent external calls by default, and a configurable maximum of 10 concurrent calls. These are not hardcoded constants: administrators can easily adjust them via `.env` without any code change or rebuild.

Configuratievariabelen:

```dotenv
MAX_GOLDEN_SET_ITEMS=10000
MAX_MODEL_PROFILES_PER_RUN=20
DEFAULT_RUN_CONCURRENCY=4
MAX_RUN_CONCURRENCY=10
```

Application validates at startup that values ​​are positive integers and that default concurrency is not greater than maximum concurrency. Active boundaries are shown in relevant UI forms. Increase may impact memory, provider rate limits and SQLite write concurrency.

- list pages are paginated;
- UI remains responsive during run;
- run status is polled a maximum of every 2 seconds;
- dashboard for 10,000 result rows loads within 3 seconds excluding first heavy aggregation on reference hardware.

### NFR-6 Audit en reproduceerbaarheid

Elke run bewaart:

- UTC timestamps in ISO 8601;
- application version/git commit if available;
- immutable snapshots of used input and configuration;
- judge template and version;
- EUR prices, cached/reasoning/fixed request rates, optional context threshold and chosen price rate at time of run;
- raw model and judge answers;
- error and retry metadata.

Non-reproducible provider factors, such as silently changed model weights, are explicitly not guaranteed.

### NFR-7 Logging

Python `logging`, with timestamp, level, component, run ID, case ID and unique execution/test ID where relevant. No `print`. Prompts, expected answers, model answers, judge input and judge output are never logged in whole or in part; they are stored unencrypted in local SQLite. The request and response bodies are also not logged in the event of exceptions and external provider errors.

### NFR-8 Taal, toegankelijkheid en browserondersteuning

- MVP interface is exclusively Dutch and has no language switch.
- Code, identifiers, API fields, logs and technical documentation are in English.
- User texts are centrally managed and not distributed hardcoded, so that later internationalization remains possible without changing domain logic.
- Keyboard controls, visible focus, labels on input fields and sufficient contrast.
- Last two versions of Chrome, Edge and Firefox.
- Desktop first; usable from 1024 px wide.

## 6. Conceptueel datamodel

| Entity | Key Relationships |
|---|---|
| `Endpoint` | has multiple `Model` records |
| `EndpointSecret` | belongs to one `Endpoint`; encrypted API key/secret headers |
| `Model` | belongs to `Endpoint`; used by `RunModelSnapshot` |
| `GoldenSet` | has multiple `GoldenSetItem` records |
| `GoldenSetItem` | contains evaluator configuration |
| `JudgeConfiguration` | refers to judge model and template version |
| `Run` | refers to golden set and contains snapshots/status |
| `RunCaseSnapshot` | immutable copy of golden-set-item per run |
| `RunModelSnapshot` | immutable copy of model, endpoint metadata and prices |
| `EvaluationResult` | one per run case/model; target response plus evaluation |
| `RequestAttempt` | zero or more per target or judge call |
| `RunJob` | sustainable queue job per run-case/model combination, with status, lease and recovery metadata |

Belangrijke constraints:

- unique: `(golden_set_id, case_id)`;
- unique: `(endpoint_id, display_name)`; multiple test profiles may use the same `model_id`;
- unique: `(run_id, run_case_snapshot_id, run_model_snapshot_id)`;
- secrets are never included in snapshots;
- amounts as decimal/string or fixed decimal database column, not float;
- JSON configuration is validated with Pydantic when read.

Final physical schedule and migrations are derived from this model during implementation.

## 7. Backend API (MVP contract in outline)

All endpoints under `/api/v1`, JSON unless file download.

- `GET/POST /endpoints`
- `GET/PATCH/DELETE /endpoints/{id}`
- `POST /endpoints/{id}/test`
- `GET/POST /endpoints/{id}/models`
- `POST /endpoints/{id}/discover-models`
- `GET/PATCH/DELETE /models/{id}`
- `GET/POST /golden-sets`
- `GET/PATCH/DELETE /golden-sets/{id}`
- `POST /golden-sets/import`
- `GET /golden-sets/{id}/export?format=json|csv`
- `GET/POST /golden-sets/{id}/items`
- `GET/PATCH/DELETE /golden-set-items/{id}`
- `GET/PATCH /judge-configuration`
- `POST /configuration/import`
- `GET /configuration/export`
- `GET/PATCH /settings`
- `GET/POST /runs` (inclusief optionele quality gates)
- `GET /runs/{id}`
- `POST /runs/{id}/cancel`
- `POST /runs/{id}/rerun-failures`
- `GET /runs/{id}/results`
- `GET /runs/{id}/export?format=json|csv|html`

Fouten volgen consistent contract:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Leesbare samenvatting",
    "details": [{"field": "name", "message": "Verplicht"}],
    "request_id": "uuid"
  }
}
```

Routes are given docstrings and the backend provides a current OpenAPI specification that is checked for validity and route consistency in CI/tests.

## 8. Schermen

1. **Dashboard:** recent runs, status and core comparisons.
2. **Endpoints:** list, add/edit, manage API key header, prefix and additional headers, replace key, set environment reference and test connection.
3. **Models:** models per endpoint, parameters and prices.
4. **Golden sets:** list, details, import/export and item editor.
5. **Judge configuration:** model, template, threshold and test option.
6. **New run:** golden set, models, summary and start confirmation.
7. **Run detail:** live progress, comparison, filters and item details.
8. **Run History:** search, open and delete.
9. **Configuration:** full JSON import/export with preview, merge/replace and missing-secret status.
10. **Settings:** Edit operational values ​​and show per value source (`GUI/database`, `.env override` or `default`).

Destructieve acties vragen expliciete bevestiging.

## 9. Validation and error categories

Standardized error categories:

- `validation_error`;
- `authentication_error` (extern 401/403);
- `rate_limit_error` (429);
- `provider_client_error` (overige 4xx);
- `provider_server_error` (5xx);
- `timeout_error`;
- `network_error`;
- `invalid_provider_response`;
- `judge_parse_error`;
- `cancelled`;
- `internal_error`.

UI shows safe error text plus enough context to restore configuration.

## 10. Teststrategie

### Unit tests

- exact match normalization and all options;
- contains with `all`/`any` and normalization;
- regexflags, search/full-match, invalid patterns and timeout;
- numeric parsing, decimal separators and absolute/relative tolerances;
- JSON parsing, code fencing, schema validation and optional deep equality;
- judge output parser, full criterion set, subscore validation, weighted total score, anomalous judge total score and threshold logic;
- cost calculation, EUR rounding, missing usage, cached/reasoning subsets without double counting, fixed request costs and selection of standard or high context rate on limit values ​​`X` and `X + 1`;
- aggregation, weights, errors and percentiles;
- quality gate evaluation on inclusive boundaries, per-model metrics, total costs and unknown/indeterminate cases;
- SSE parser with fragmented events, `[DONE]`, final usage, broken streams and partial text;
- monotonic time measurement and calculation of total latency, TTFT and TPS including unknown edge cases;
- validation of URL, headers, prices, imports and all configurable input/response size limits;
- encryption/decryption and editing of secrets;
- log editing: prompts, answers and provider bodies do not appear in logs, even in case of errors; correlation via execution/test ID;
- retry classification and backoff without real waiting time;
- endpoint limiter for RPM, TPM, concurrency and `Retry-After`.

### Integratietests

- CRUD and database constraints;
- JSON/CSV round trip;
- mocked OpenAI-compatible target, judge and model discovery endpoints;
- model discovery, URL proposal, selective import and maintenance of existing profiles;
- successful run with multiple models/evaluators;
- partial errors, retries and cancellations;
- restart recovery with expired leases, retention of completed jobs and resumption of uncompleted jobs;
- transactional result storage plus job completion prevents local duplicate results;
- snapshots remain the same after configuration changes;
- configuration export/import round-trip, merge/replace, conflict mapping, schema versions and missing secrets;
- endpoint and custom header management via GUI and JSON, without secret values ​​in export;
- settings priority `.env > GUI/database > default` and read-only display of overrides;
- JSON, CSV and HTML exports contain data but no secrets; HTML is offline, escaped and printable;
- retention cleanup deletes only expired terminal runs and all dependent data atomically; `0` stores indefinitely;

### End-to-end rooktest

Configure two mocked models, import fixture set, perform run and check dashboard/export.

Test suite must pass without real API key or network access.

## 11. Oplevercriteria

Implementatie is gereed wanneer:

- all MVP requirements have been implemented;
- `pytest` complete success;
- frontend build/lint/typecheck passes if applicable;
- database can be initialized and migrated;
- `.venv/`, `.env`, database and generated exports are ignored by git;
- `.env.example` contains all variables without real secrets;
- local `.env` exists in accordance with `agent.md`;
- `README.md` describes installation, configuration sources and priority, JSON schema, starting, testing, import format, security and limitations;
- sample golden set and mock/smoke test are available;
- application starts from empty checkout with documented commands;
- no API key is visible in server-generated HTML, API responses, logs or exports; secret entry is only visible during entry into local GUI password field.

## 12. Beslisregister MVP v1.0

The product and implementation decisions below are final for MVP v1.0:

1. **Usage form — RESOLVED:** MVP is a local single-user web app without accounts, roles or central deployment.
2. **Endpoint Protocol — RESOLVED:** endpoints are OpenAI chat completions-compatible. Temperature and reasoning are part of the model test profile. Reasoning uses a configurable field name, default values ​​`off`, `low`, `medium`, `high` and a free custom value.
3. **Prompt structure — RESOLVED:** each golden-set item has one optional system prompt and exactly one user prompt. Multi-turn is outside of MVP; architecture remains prepared for it.
4. **Judge — RESOLVED:** one global default judge profile, optional override per golden set and per run. An item can only adjust rubric, threshold and context, not the judge model. Standard rubric rates factual accuracy, completeness, relevance, instructional compliance, and consistency, each standard given equal weight. Judge returns score and reason per criterion; application calculates weighted total score. Scoring scale is `0.0–1.0`, default threshold `0.7`.
5. **Other evaluators — RESOLVED:** MVP contains, in addition to exact match and LLM judge, contains, regex, numerical comparison with tolerance and JSON Schema validation. Semantic similarity falls outside of MVP.
6. **Endpoint Headers and Contract — RESOLVED:** Multiple additional custom headers are supported and can be individually marked secret. API key prefix is ​​configurable, for example empty or `Bearer `. Request body and response paths remain fixed per the OpenAI contract, except for explicitly configurable provider parameters such as reasoning.
7. **Discover Models — RESOLVED:** Models can be added manually or retrieved via an optional configurable models URL. UI can safely suggest `/models` based on completions URL. User imports selected models; existing profiles are never automatically overwritten.
8. **Costs — RESOLVED:** costs are always in EUR. Standard input/output prices, optional cached input and reasoning rates, optional fixed request costs and optionally an adjustable context threshold `X` apply per model profile with different rates when input tokens are larger than `X`. The complete request then uses the high rate. Token subsets are not double taxed.
9. **Metrics and Ranking — DECIDED:** Primary ranking is highest weighted average score, lowest error rate, then lowest total cost. UI also shows pass rate, total latency, TTFT, TPS, input/output/cached/reasoning tokens, target/judge/total costs and costs per passed case. Target calls use streaming to measure TTFT and TPS.
10. **Run scale — RESOLVED:** defaults are 10,000 items per golden set, 20 model test profiles per run, 4 parallel calls by default and 10 parallel calls maximum. All boundaries are easily adjustable via `.env`; MVP remains single-user.
11. **Rate limits — RESOLVED:** requests per minute, tokens per minute and maximum concurrent requests are configurable per endpoint. Empty means unlimited by application. HTTP 429 uses `Retry-After` or exponential backoff with jitter.12. **Data and Privacy — RESOLVED:** prompts and responses may be sensitive. They are stored unencrypted in local SQLite and never recorded in logs. Logs only use run ID, case ID and unique execution/test ID. Retention is configurable via `RUN_RETENTION_DAYS`; empty or `0` stores indefinitely. Runs can be completely deleted manually. Export warns of sensitive content.13. **Network/security — RESOLVED:** HTTPS is required by default. HTTP is allowed for localhost/loopback and for internal endpoints that are explicitly marked as trusted. API keys and secret headers are stored locally encrypted; redirects to other hosts/protocols are not automatically followed.
14. **Results Reporting — RESOLVED:** exports support JSON, CSV and a standalone offline HTML report with print CSS. Native PDF generation is outside of MVP; user can print HTML to PDF via browser.
15. **Restart recovery — RESOLVED:** durable SQLite jobqueue automatically resumes active runs. Completed combinations are not repeated; uncompleted or interrupted jobs are. Repetition of a provider call active during a crash can cause double provider costs and will be visibly marked.
16. **Language — RESOLVED:** MVP interface is Dutch only, without language switch. Code, API fields, logs and technical documentation are in English. UI texts are centrally managed for later internationalization.
17. **Deployment — RESOLVED:** Docker Compose is primary launch method. Launching directly via Python `.venv` on Windows and Linux remains supported.
18. **Quality gates — RESOLVED:** optional gates support minimum score, minimum pass rate, maximum error rate, average/p95 total latency, p95 TTFT, average TPS and maximum total EUR costs. Gates are golden-set defaults with run override. The result is `not_evaluated`, `passed`, `failed` or `indeterminate`.

## 13. Definitieve MVP-defaults

- Local, single-user Flask web app without login (**DECIDED**).
- Dutch UI without language switch; code, API, logs and technical documentation in English; UI texts prepared for later internationalization (**DECIDED**).
- OpenAI-compatible chat completions via fully configurable URL; target calls use SSE streaming, judge calls remain non-streaming (**DECIDED**).
- Temperature and reasoning are set per model test profile, so that the same external model can be compared with multiple configurations (**DECIDED**).
- Reasoning field name is configurable; supported default values ​​are `off`, `low`, `medium`, `high`, supplemented with a free custom value (**DECIDED**).
- Endpoints, API key header/prefix and additional custom headers can be managed via GUI and configuration JSON; secret values ​​are never exported (**DECIDED**).
- Functional configuration uses GUI + JSON. Operational settings use GUI + `.env`, with priority `.env > GUI/database > default` (**DECIDED**).
- Models are added manually or selectively imported via a configurable OpenAI-compatible models URL; existing profiles are never overwritten (**DECIDED**).
- HTTPS is required by default; HTTP only locally or for explicitly trusted internal endpoints. OpenAI request/response paths are fixed, except for explicit provider parameters (**RESOLVED**).
- One global standard judging profile, optional override per golden set and run; item can only adjust rubric, threshold and context. Standard rubric has five criteria, sub-scores and sub-reasons; total score is weighted and threshold is `0.7` (**DECIDED**).
- MVP evaluators are exact match, contains, regex, numeric, JSON Schema and LLM judge; semantic similarity falls outside of MVP (**DECIDED**).
- Costs are always in EUR. Model profiles support input/output, cached input, reasoning, fixed request costs and optionally a context threshold with high context rates; token subsets are not double charged (**DECIDED**).
- Sustainable SQLite job queue with worker leases; no external queue. Active runs will be resumed after restart (**DECIDED**).
- Docker Compose is primary deployment; Python `.venv` on Windows and Linux is a supported alternative (**DECIDED**).
- Scale defaults: 10,000 items per set, 20 model profiles per run, concurrency default 4 and maximum 10; all boundaries are adjustable via `.env` (**DECIDED**).
- RPM, TPM and maximum concurrency are optionally configurable per endpoint; empty means no application limit (**DECIDED**).
- Prompts and answers are unencrypted in local SQLite, never in logs; referencing logs via run, case, and execution/test IDs (**DECIDED**).
- Run retention is configurable; unlimited by default. Manual removal remains possible and export warns for sensitive content (**RESOLVED**).
- Golden-set items contain one optional system prompt and exactly one user prompt; multi-turn is excluded from MVP (**DECIDED**).
- JSON and CSV for golden-set import/export; run results additionally as a standalone HTML report with print CSS, without native PDF generation (**DECIDED**).
- Optional quality gates assess score, pass rate, errors, latency, TTFT, TPS and costs; missing required metrics return `indeterminate` (**DECIDED**).- Input and response size limits are customizable via `.env` with defaults of 1,000,000 prompt characters, 100,000 system/judge context characters, 1,000,000 expected response characters, 10,000,000 model response characters, and 100 MiB for import/provider response (**DECIDED**).- All times in UTC, displayed in UI in local browser time.
