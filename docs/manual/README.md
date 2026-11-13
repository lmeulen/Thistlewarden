<a id="manual-top"></a>

# Thistlewarden User Manual

Use this index to open a focused manual topic. For environment variables and release procedures, see [configuration](../configuration.md) and [release guide](../release.md).

## Topics

- [Getting started and common workflows](getting-started.md) — Introduction, quick start, dashboard, and recurring workflows.
- [Endpoints and model profiles](endpoints-and-models.md) — Provider access, authentication, model settings, and pricing.
- [Golden sets and test cases](golden-sets.md) — Dataset lifecycle, test-case authoring, ordering, and deletion.
- [Evaluation methods](evaluators.md) — Exact match, contains, regex, numeric, JSON Schema, and LLM judge cases.
- [Judge profiles](judges.md) — Judge models, inference settings, presets, and assignment.
- [Runs and results](runs-and-results.md) — Starting, monitoring, cancelling, retrying, and interpreting runs.
- [Quality gates](quality-gates.md) — Release thresholds and gate outcomes.
- [Import, export, and backup](import-export.md) — Golden-set and configuration exchange, reports, and backups.
- [Operations, security, and privacy](operations.md) — Settings, restart behavior, secrets, and sensitive data.
- [Troubleshooting](troubleshooting.md) — Provider, evaluator, judge, and interrupted-run diagnosis.

## 19. Glossary

Key terms are first explained in [Core concepts](getting-started.md#14-core-concepts). Use this glossary as reference for abbreviations and detailed meanings.

### 19.1 Terms and abbreviations

| Term | Meaning |
| --- | --- |
| **Active** | Available for new work. An active model profile also requires active parent endpoint; enabled case also requires active golden set. |
| **API key** | Provider credential used by endpoint authentication. Treat as secret; UI masks it and environment-managed secrets are not editable there. |
| **Case ID** | Stable, user-defined identifier for test case within golden set. Preserve it for same conceptual behavior across versions. |
| **Candidate output** | Answer returned by target model for one case. |
| **Completion** | Provider model call producing candidate output. |
| **Endpoint** | Provider connection definition: URLs, authentication method/secret reference, timeout, limits, and rate-limit settings. |
| **Evaluator** | Method that scores candidate output against case criteria: Exact match, Contains, Regex, Numeric, JSON Schema, or LLM judge. |
| **Golden set** | Versioned collection of test cases used as evaluation suite. |
| **Judge profile** | Saved LLM-judge configuration containing judge model, rubric, threshold, temperature, output limit, and active/default state. |
| **LLM judge** | Evaluator that asks configured judge model to assess response using rubric. It adds provider dependency, latency, cost, and possible variation. |
| **Model profile** | Target-model request configuration, including model ID, generation parameters, token field behavior, and configured rates. |
| **P95** | 95th percentile. P95 latency/TTFT means 95% of observed values are at or below displayed value. |
| **Quality gate** | Configured run acceptance thresholds evaluated after terminal run. Outcome is passed, failed, or indeterminate. |
| **Result** | Stored outcome for one case/model job, including status, score, evaluation detail, and available metrics. |
| **Run** | Evaluation execution over enabled case snapshots and selected model snapshots. One run can contain multiple target models. |
| **Snapshot** | Immutable copy of case, model, judge, and gate data captured for run, preserving historical meaning after live configuration changes. |
| **TTFT** | Time to first token: elapsed time before first streamed candidate content. |
| **TPS** | Tokens per second during generation when provider usage and timing allow calculation. |
| **Weighted score** | Aggregate score that accounts for case weights; interpret with job/error status and evaluator mix. |

[Back to top](#manual-top)
