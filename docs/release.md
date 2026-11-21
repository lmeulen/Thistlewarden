# Release and offline E2E procedure

## Local quality gate

Install [Gitleaks](https://github.com/gitleaks/gitleaks), then run the quality and full-history secret gates:

```bash
gitleaks git . --redact --config .gitleaks.toml
python -m pip install -r requirements.txt
python -m pip install -e .
python -m pip check
python -m pip_audit -r requirements.txt --progress-spinner off
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest -q tests/unit tests/integration --cov=evaluator --cov-branch --cov-report=term-missing:skip-covered
python -m build --wheel --sdist --outdir dist
python scripts/verify_manual_artifacts.py --wheel dist/thistlewarden-*.whl --sdist dist/thistlewarden-*.tar.gz
```

Review every secret-scanner finding. `.gitleaks.toml` allows only one exact synthetic fixture used by redaction tests; do not broaden it to paths or commits.

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

- `docs/manual/` is sole authored manual tree. Source and editable checkouts serve that fixed repository location; wheel, source distribution, and container builds install same files under `share/thistlewarden/manual`. No synchronization or generated copy under `src/` is permitted.
- Python package version and Compose image tag must match.
- Runtime dependencies are exact pins in `pyproject.toml`; `requirements.txt` mirrors runtime and dev gates.
- Never publish `.env`, database, WAL, exports, backups or compose logs containing user content.
- Tag only after Python and Docker jobs pass on the exact release commit.
- Version `0.3.0` is published as a beta; release notes must preserve that maturity label.

## Upgrade proof

Integration tests migrate empty schema and historical migration fixtures to current schema. Existing database upgrades create unique pre-upgrade backup. Restore test uses SQLite backup API, validates integrity, then migrates restored copy.

## Rollback

No down migrations. Stop new version, select pre-upgrade backup as new `DATABASE_PATH`, run previous image/package, and retain failed-upgrade files for diagnosis. Configuration schema import is version-bound; unsupported future schema is rejected before writes.
