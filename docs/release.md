# Release and offline E2E procedure

## Local quality gate

```bash
python -m pip install -r requirements.txt
python -m pip install -e .
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest -q tests/unit tests/integration
```

## Docker release gate

Create `.env` from `.env.example`, replace both placeholder secrets, then:

```bash
docker compose -f compose.yaml -f compose.e2e.yaml config --quiet
docker compose -f compose.yaml -f compose.e2e.yaml build --pull
docker compose -f compose.yaml -f compose.e2e.yaml up -d --wait
python scripts/e2e_release.py
docker compose -f compose.yaml -f compose.e2e.yaml logs --no-color > compose.log
! grep -F "release-fixture-secret" compose.log
docker compose -f compose.yaml -f compose.e2e.yaml down -v
```

E2E uses no external provider. It imports versioned configuration containing two mock models and all six evaluator methods, stores a fixture API key, executes streaming target and non-streaming judge calls, waits for terminal run, checks ranking/gates, exports JSON/CSV/HTML, and scans responses for fixture secret.

Manual release addition: restart evaluator during second E2E run and verify `recovery_warning`; then test retention/delete through GUI. CI performs clean image build and automated path. Docker engine is required; static asset tests are not substitute for actual build.

## Versioning and artifacts

- Python package version and Compose image tag must match.
- Runtime dependencies are exact pins in `pyproject.toml`; `requirements.txt` mirrors runtime and dev gates.
- Never publish `.env`, database, WAL, exports, backups or compose logs containing user content.
- Tag only after Python and Docker jobs pass.

## Upgrade proof

Integration tests migrate empty schema and historical migration fixtures to current schema. Existing database upgrades create unique pre-upgrade backup. Restore test uses SQLite backup API, validates integrity, then migrates restored copy.

## Rollback

No down migrations. Stop new version, select pre-upgrade backup as new `DATABASE_PATH`, run previous image/package, and retain failed-upgrade files for diagnosis. Configuration schema import is version-bound; unsupported future schema is rejected before writes.
