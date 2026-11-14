<a id="page-top"></a>

# Quality gates

[Top of page](#page-top) · [Manual contents](README.md#manual-top)


### Purpose of quality gates

Quality gates convert run metrics into explicit acceptance checks. They assess aggregate results after run completes, helping teams decide whether model/version is acceptable for release, regression baseline, or cost/performance target.

Gates can check weighted score, pass rate, error rate, mean latency, P95 latency, P95 time to first token (TTFT), mean tokens per second (TPS), and total cost. All configured checks must pass for run gate status to pass.

Gates do not stop an in-progress run, alter individual result scores, or replace human review. They evaluate recorded run data. Use them as reproducible policy controls alongside inspection of failed cases, provider errors, costs, and changed test material.

Set defaults on golden set to apply same release criteria to future runs. At run creation, optionally replace those defaults for one run. Thistlewarden snapshots selected gates at start, so later changes do not rewrite historical gate outcomes.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Configuring thresholds

Open saved golden set and find **Quality gates**. Enter only checks you want to enforce, then select **Save gates**. Empty field means that gate is not configured. Start with one quality gate after you have stable baseline results; quality gates are advanced release-policy controls, not required for first run.

**Table summary:** score/pass/error/latency/TTFT/TPS checks apply to each model. Total-cost check applies to entire run.

| Gate | Pass condition | Scope |
|---|---|---|
| Min. weighted score | score is at least threshold | Each model |
| Min. pass rate | pass rate is at least threshold | Each model |
| Max. error rate | error rate is no more than threshold | Each model |
| Max. mean latency ms | mean latency is no more than threshold | Each model |
| Max. P95 latency ms | P95 latency is no more than threshold | Each model |
| Max. P95 TTFT ms | P95 TTFT is no more than threshold | Each model |
| Min. mean TPS | mean throughput is at least threshold | Each model |
| Max. total cost | known total cost is no more than threshold | Entire run |

Score, pass-rate, and error-rate thresholds are decimals from `0` to `1`: use `0.90` for 90%, not `90`. Latency and TTFT are milliseconds. TPS is tokens per second. Cost uses configured profile currency/unit rates; use it as budget guardrail, not provider invoice reconciliation.

In **New run**, leave **Completely replace golden set defaults** unchecked to use golden-set gates. Check it to use values entered on run form instead; leaving every override value empty deliberately creates run with no gates. Run override replaces whole defaults, not selected individual fields.

Begin with few meaningful gates. For example, require weighted score at least `0.85`, error rate no more than `0.02`, and total cost no more than approved budget. Calibrate thresholds from known-good baseline runs and revise them through normal change review.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Passed, failed, and indeterminate states

Gate evaluation occurs after run reaches `completed` or `completed with errors`. Runs still pending, running, cancelled, or otherwise non-terminal have `not evaluated` status. A completed run with no configured gates also has `not evaluated` status.

For each configured gate:

- **Passed**: measured value is available and meets inclusive threshold. A minimum passes at or above threshold; a maximum passes at or below it.
- **Failed**: measured value is available but violates threshold.
- **Indeterminate**: required measured value is unavailable.

Overall run gate status is **failed** if any gate fails. If none fail but one or more are indeterminate, overall status is **indeterminate**. It is **passed** only when every configured gate passes.

Missing latency, TTFT, TPS, score, pass-rate, error-rate, or cost data can make relevant gate indeterminate. Total-cost gate is indeterminate when any model total cost is unknown. Treat indeterminate as insufficient evidence, not as pass. Investigate missing metrics, provider errors, or incomplete cost configuration before release decision.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Interpreting gate results

Run detail page shows overall gate status near run status, per-model gate dots in comparison table, and **Quality gates** table. Table lists gate name, model or **Entire run**, measured value, threshold, visualization, and outcome.

Read gates in context:

1. Start with overall status. Failed means at least one configured policy condition was violated.
2. Find failed or indeterminate table rows and identify affected model.
3. Compare measured value with threshold; thresholds are inclusive.
4. Inspect model aggregates and individual results to explain metric change.
5. For total cost, check known-cost counts before treating budget result as complete.
6. Decide whether to fix model/configuration/test issue, rerun, or approve documented exception outside application.

Do not loosen threshold solely to make one run pass. First determine whether regression is real, test suite changed, provider behavior changed, or metric is unsuitable. If criteria legitimately change, update golden-set defaults, version/document change, and run new baseline. Historical runs keep original gate snapshot and remain comparable as records of prior policy.

Exports include stored gate results with run data. Preserve them with release evidence when gates support formal acceptance decisions.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)
