<a id="manual-top"></a>

# Thistlewarden User Manual

Use this manual to connect models, build evaluations, run comparisons, and understand results.

![Thistlewarden dashboard showing evaluation, configuration, and test-material summaries](images/dashboard.png)

*Thistlewarden overview with synthetic demonstration data. See [Understand the dashboard](dashboard.md) for details.*

## Start here

- [Run your first evaluation](getting-started.md#quick-start) — Connect one model, create one test, and inspect the result.
- [Set up Thistlewarden](getting-started.md#set-up-thistlewarden) — Open the application and verify initial configuration.
- [Understand the dashboard](dashboard.md) — See recent runs, evaluation coverage, pricing, and shortcuts.

## Build evaluations

- [Connect endpoints and models](endpoints-and-models.md) — Add provider access, model settings, and pricing.
- [Create golden sets and test cases](golden-sets.md) — Build and maintain reusable evaluation suites.
- [Choose an evaluation method](evaluators.md) — Compare exact text, fragments, patterns, numbers, JSON, or judged quality.
- [Configure an LLM judge](judges.md) — Select judge models, inference settings, and immutable presets.
- [Configure quality gates](quality-gates.md) — Decide when run quality is acceptable.

## Run and review

- [Start and manage runs](runs-and-results.md#running-evaluations) — Select models, monitor progress, cancel work, or retry failures.
- [Automate runs from command line](runs-and-results.md#automating-runs-from-command-line) — Look up stable IDs, run quality gates, export reports, and use CI exit codes.
- [Understand results](runs-and-results.md#understanding-results) — Read scores, metrics, charts, costs, and individual evaluations.
- [Follow common workflows](common-workflows.md) — Compare models, build regression suites, and preserve history.
- [Import, export, and back up data](import-export.md) — Move test sets, exchange configuration, export reports, and protect data.

## Manage Thistlewarden

- [Change application settings](settings.md) — Understand editable values, environment overrides, secrets, and restarts.
- [Protect security and privacy](security-and-privacy.md) — Handle credentials, prompts, answers, exports, and local access safely.
- [Configuration reference](../configuration.md) — Review environment variables, runtime limits, and schedules.
- [Release guide](../release.md) — Run release checks and offline end-to-end verification.
- [Develop custom LLM judges](../judge-extensions.md) — Implement, register, configure, test, and version trusted local judge extensions.

## Get help

- [Troubleshoot problems](troubleshooting.md) — Diagnose provider, authentication, evaluator, judge, and interrupted-run issues.
