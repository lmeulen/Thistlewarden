<a id="page-top"></a>

# Common workflows

[Top of page](#page-top) · [Manual contents](README.md#manual-top)


## First evaluation from start to finish

Use this small, low-risk workflow to establish working baseline:

1. Create provider endpoint with approved non-production credential; keep endpoint active.
2. Create active model profile, confirm model ID and request parameters, then use **Test model**.
3. Create active golden set named and versioned for purpose, for example `Support baseline` version `0.1`.
4. Add three to ten enabled cases representing key behaviors. Start with Exact match, Contains, Numeric, or JSON Schema.
5. Use **Test evaluator** with known passing and failing candidate output for each deterministic case.
6. Optionally set simple quality gates after you know expected baseline; do not guess strict thresholds initially.
7. Open **Runs**, select golden set and one model profile, enter descriptive run name, and inspect preflight size/cost warning.
8. Start run and wait for terminal status.
9. Review weighted score, failed/error results, latency, and known cost. Open individual results to verify evaluator behavior.
10. Export results or record run name/ID before changing suite or profile.

Fix configuration or test intent before scaling to many models/cases. A passing connection test proves basic provider call path, not that prompts, evaluator criteria, price configuration, or quality expectations are correct.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

## Comparing multiple models

Select two or more active target model profiles in one new run. Thistlewarden runs every enabled case against every selected model, producing comparable result matrix under same run snapshots. Run preview shows `active cases × selected models` job count.

For useful comparison, hold constant golden set, enabled cases, evaluator configuration, judge selection, and quality gates. Give run name clear comparison scope, for example `Support 1.2 — model A vs model B`. Use same provider settings where meaningful, but preserve model-specific token/reasoning fields when provider requires them.

After completion, use **Model comparison** for weighted score, P95 latency, P95 TTFT, mean TPS, token totals, costs, and gate indicators. Use cost-versus-score chart to identify models omitted due to unknown cost/score. Filter results by model and case to explain aggregate differences. Compare error counts before treating score differences as quality differences.

More models multiply provider calls and potential cost. Start with small suite, avoid changing live configuration while comparison runs, and ensure configured price rates are current enough for planning. Run snapshots let a completed comparison remain auditable even after profile edits.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

## Building regression suites

A regression suite is stable golden set representing behaviors that must not degrade. Build it incrementally from real defects, critical customer tasks, safety requirements, structured-output contracts, and known edge cases.

Recommended practice:

1. Define suite purpose, owner, version, category vocabulary, and entry criteria.
2. Add focused cases with stable IDs; one primary behavior per case.
3. Use categories and tags to cover product areas and support filtered investigation.
4. Weight critical cases deliberately; do not inflate scores by overweighting everything.
5. Prefer deterministic evaluators for objective contracts and test examples before saving.
6. Add LLM judge only for quality that cannot be expressed deterministically.
7. Run suite against known-good baseline and inspect every surprising pass/fail.
8. Configure calibrated gates once baseline is trustworthy.
9. Run suite before release or model/configuration change and retain run exports as evidence when needed.

Add a case when it protects meaningful behavior; remove or disable only with documented reason. Keep cases representative and non-sensitive. Duplicate golden set to branch major release or policy change, rather than silently redefining historical baseline.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

## Combining deterministic and LLM-based evaluation

Use deterministic methods and LLM judge in same golden set, selecting each based on requirement. Deterministic checks are repeatable, fast, local, and do not make extra provider call. LLM judge handles relevance, completeness, reasoning quality, and other nuanced criteria, but adds cost, latency, provider dependency, and variation.

Example composition:

- JSON Schema validates required response shape.
- Exact match or Numeric validates fixed field/value.
- Contains verifies mandatory warning or citation.
- LLM judge evaluates usefulness and completeness of longer answer.

Make pass criteria explicit. Do not use LLM Judge where JSON Schema or exact rule is sufficient. Do not use deterministic fragments as proxy for overall quality when semantic assessment is required. For LLM Judge, publish calibrated immutable Judge version and configure active Judge profile with suitable model/inference settings. Remember Judge provider receives candidate and evaluation context; use approved provider and non-sensitive material.

Review results by **Judge type** in run detail. Investigate disagreement between methods: it may reveal prompt ambiguity, brittle deterministic rule, weak Judge instructions, or target behavior issue. Gate thresholds aggregate all results, so confirm weighting and evaluator mix represent actual release policy.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

## Updating tests while preserving historical results

Runs store immutable snapshots of selected case content, model configuration, judge profile, and quality gates at run creation. Editing a live golden set or model profile changes future runs only; it does not rewrite completed result data.

For routine update, edit case, increment golden-set version, record reason in description/tags or team change record, test evaluator, and start new named run. Keep stable Case ID when test remains same conceptual behavior, so reviewers can follow it across versions. Create new ID when behavior or acceptance contract changes materially.

For broad or risky change, duplicate golden set first. Rename and version duplicate, modify copy, run it, and retain original active only until replacement is approved. Duplication includes cases, judge override, and quality-gate defaults but creates independent identifiers.

Do not modify historical exports to simulate new baseline. Keep prior run and export for audit, then compare new run metrics/results with prior evidence manually. Deactivate obsolete cases or sets when needed; do not delete material merely to hide previous failures. Existing run snapshots remain available until run itself is deleted under retention policy.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)
