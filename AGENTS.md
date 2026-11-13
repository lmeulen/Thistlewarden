# AGENTS.md

Guidance for coding agents working on Thistlewarden. Keep this file concise and current. Do not add temporary status, issue lists, test counts, or release snapshots.

## Project

Thistlewarden is a local, single-user web application for reproducible evaluation of OpenAI-compatible language models.

Core stack:

- Python 3.12+
- Flask and server-rendered HTML
- Pydantic
- SQLite on a local filesystem
- Bounded worker threads in one application process
- Ruff, strict Mypy, Pytest, and pytest-xdist

Read [README.md](README.md), [user manual](docs/manual/README.md), and [security policy](SECURITY.md) before changing behavior or deployment assumptions.

## Architecture boundaries

Preserve these constraints unless user explicitly requests a product-level change:

- One host and exactly one application process.
- SQLite, WAL, exports, and backups use a local filesystem; no network filesystem.
- Host-facing deployment remains loopback-only.
- No authentication or multi-user isolation exists.
- Provider calls happen server-side only.
- Target calls stream; LLM-judge calls are non-streaming.
- Runtime concurrency uses bounded threads.
- Domain scores, weights, costs, prices, and token-rate calculations use `Decimal`, not `float`.
- Persistent timestamps use timezone-aware UTC; durations and deadlines use monotonic time.
- GUI, API, logs, and technical documentation use English.
- Avoid heavy frontend frameworks and external chart dependencies.

## Evaluation contracts

- Deterministic evaluators must return auditable inputs, constraints, options, and diagnostics.
- JSON Schema behavior follows Draft 2020-12. Non-standard comparison options must not silently change schema semantics.
- LLM-judge testcases select immutable, explicitly versioned presets.
- Testcases must not define judge rubrics, thresholds, weights, or prompt templates.
- Judge execution profiles select model and inference settings; they do not define evaluation semantics.
- Temperature and reasoning overrides are mutually exclusive.
- Historical completed-run snapshots must remain readable.
- Never silently reinterpret or convert legacy evaluation semantics.

## Security and privacy

- Never print, log, expose, or commit secrets.
- Keep secret fields suppressed in GUI, APIs, exports, logs, and configuration errors.
- Do not weaken `SECRET_KEY` or encryption-key validation for tests.
- Treat prompts, expected answers, model answers, databases, backups, and exports as sensitive.
- Preserve CSRF and explicit confirmation on destructive GUI actions.
- Keep endpoint SSRF controls, redirect blocking, and ambient-proxy blocking intact.
- Partial streaming responses must never be replayed automatically.
- Follow [SECURITY.md](SECURITY.md) for supported deployment boundary and residual risks.

## Database and migrations

- Never modify a persistent database without first making a backup.
- Migrations must be ordered, deterministic, and tested from representative prior schemas.
- Preserve transaction boundaries; avoid APIs such as `executescript()` when atomic migration behavior matters.
- After direct database work, run `PRAGMA integrity_check` and `PRAGMA foreign_key_check`.
- Do not delete records referenced by run history. Archive or reject deletion where required.
- Do not edit or commit local runtime files under `data/`.

## Change discipline

- Inspect current code and tests before editing; archived documents are non-normative.
- Make focused changes and preserve unrelated local modifications.
- Do not reset, clean, amend, rebase, or force-push without explicit instruction.
- Do not commit or push unless requested.
- Use exact user-provided commit messages.
- Update tests and current documentation when contracts or user-visible behavior change.
- Keep root directory limited to essential project, security, agent, build, and dependency files.
- Historical documentation belongs under ignored `docs/archive/`, not in current documentation.

## Development commands

POSIX virtual environment usually uses `.venv/bin/python`; this repository may also be used from WSL with Windows interpreter `venv/Scripts/python.exe`. When import resolution needs it, set `PYTHONPATH=src`.

```bash
PYTHONPATH=src python -m ruff check .
PYTHONPATH=src python -m ruff format --check .
PYTHONPATH=src python -m mypy src
PYTHONPATH=src python -m pytest -q
```

Pytest is configured for eight work-stealing workers. Prefer targeted tests while developing, then run relevant integration tests and full suite before committing broad changes.

Also run:

```bash
git diff --check
```

For migration, release, or deployment changes, follow [docs/release.md](docs/release.md) and run applicable offline end-to-end checks.

## Documentation precedence

Use this order when sources disagree:

1. Current source code and tests.
2. [SECURITY.md](SECURITY.md) for security boundary.
3. [docs/configuration.md](docs/configuration.md) for configuration contracts.
4. [docs/manual/README.md](docs/manual/README.md) for user and operator behavior.
5. [docs/release.md](docs/release.md) for release procedure.
6. Files under `docs/archive/` only for historical context.
