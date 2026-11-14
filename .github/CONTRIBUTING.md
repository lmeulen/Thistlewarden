# Contributing to Thistlewarden

Thank you for helping improve Thistlewarden. The project welcomes focused bug reports, documentation fixes, tests, and code contributions.

## Before opening an issue

- Search existing issues first.
- Use the bug or feature-request form.
- Do not include API keys, `.env` files, databases, exports, prompts, model answers, customer data, or other sensitive content.
- Report suspected vulnerabilities privately as described in [SECURITY.md](../SECURITY.md).

## Issue labels

Maintainers classify each triaged open issue on three required axes:

- **Type** — exactly one of `type:bug`, `type:feature`, `type:enhancement`, `type:maintenance`, or `type:documentation`.
- **Priority** — exactly one of `prio:critical`, `prio:H`, `prio:M`, or `prio:L`. Priority expresses user and project impact, not implementation order or effort.
- **Area** — exactly one primary `area:*` label. Use the area owning most of the change; mention secondary impact in the issue instead of adding several area labels.

Add one `effort:*` label (`effort:S`, `effort:M`, or `effort:L`) only after enough investigation to estimate scope. Effort is relative and never changes priority.

Priority abbreviations are uppercase: `H` means high, `M` medium, and `L` low. `critical` remains spelled out to avoid confusing it with effort sizing.

| Axis | Meaning |
|---|---|
| `type:bug` | Existing supported behavior is incorrect or broken. |
| `type:feature` | New user-facing capability or workflow. |
| `type:enhancement` | Material improvement to existing user-facing behavior. |
| `type:maintenance` | Internal quality, test, build, dependency, or operational work. |
| `type:documentation` | Documentation-only change. |
| `prio:critical` | Release-blocking, data-loss, or severe supported-boundary security risk; act immediately. |
| `prio:H` | Major user workflow, reliability, security, or cost impact; target next. |
| `prio:M` | Meaningful improvement or bounded risk; schedule normally. |
| `prio:L` | Limited impact, uncommon edge case, or exploratory work. |
| `effort:S` | Focused change, normally a few files and tests. |
| `effort:M` | Cross-layer change requiring design and integration tests. |
| `effort:L` | Major design, migration, or multi-stage delivery. |

Canonical areas are `area:api`, `area:cli`, `area:costs`, `area:exports`, `area:golden-sets`, `area:providers`, `area:runs`, `area:security`, `area:settings`, `area:testing`, and `area:ux`. Add a new area only when several issues need a stable owner that none of these labels represent.

GitHub state, assignee, milestone, and pull-request links represent workflow. Do not add labels such as `in progress`, `blocked`, or `done`. Standard labels such as `duplicate`, `invalid`, `question`, `wontfix`, `good first issue`, and `help wanted` are optional signals, not classification axes. Dependency automation labels apply to pull requests. Intake forms may initially add `bug` or `enhancement`; maintainers replace that broad intake label with canonical `type:*` during triage.

Closed historical issues do not require retroactive relabeling. Reopened issues must follow current policy.

## Development setup

Thistlewarden requires Python 3.12 and uses a `src` layout.

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

On PowerShell, activate with `.\.venv\Scripts\Activate.ps1`.

Copy `.env.example` to `.env` only for local application runs. Generate new local secrets; never commit `.env`.

## Quality checks

Run all checks before submitting a pull request:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest -q tests/unit tests/integration
```

Changes affecting packaging, startup, providers, workers, persistence, or release behavior should also pass the Docker release gate in [docs/release.md](../docs/release.md).

## Change guidelines

- Keep changes focused and explain user-visible behavior.
- Add or update tests for behavioral changes.
- Preserve the local, single-user deployment boundary.
- Never weaken secret redaction, CSRF protection, endpoint validation, or loopback defaults.
- Do not add compatibility fallbacks that silently change evaluation meaning.
- Keep evaluator results self-contained and auditable.
- Treat database migrations as forward-only. Back up and verify realistic upgrade paths.
- Keep current user documentation under `docs/manual/` in sync with application behavior.
- Use synthetic, non-sensitive test data only.

## Pull requests

Describe:

- the problem and approach;
- user-visible changes;
- security, privacy, migration, and compatibility effects;
- tests and manual verification performed;
- related issues.

By contributing, you agree that your contribution is licensed under the Apache License 2.0.
