# Contributing to Thistlewarden

Thank you for helping improve Thistlewarden. The project welcomes focused bug reports, documentation fixes, tests, and code contributions.

## Before opening an issue

- Search existing issues first.
- Use the bug or feature-request form.
- Do not include API keys, `.env` files, databases, exports, prompts, model answers, customer data, or other sensitive content.
- Report suspected vulnerabilities privately as described in [SECURITY.md](../SECURITY.md).

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
