# Thistlewarden

[![CI status](https://github.com/lmeulen/Thistlewarden/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/lmeulen/Thistlewarden/actions/workflows/ci.yml?query=branch%3Amaster) [![Coverage on master](https://codecov.io/gh/lmeulen/Thistlewarden/branch/master/graph/badge.svg)](https://codecov.io/gh/lmeulen/Thistlewarden/branch/master) [![Python 3.12 or newer](https://img.shields.io/badge/Python-3.12%2B-3776AB)](#requirements) [![License: Apache 2.0](https://img.shields.io/badge/License-Apache--2.0-2C3E50)](LICENSE) [![User manual](https://img.shields.io/badge/User_manual-Read-2C3E50)](docs/manual/README.md) [![Security policy](https://img.shields.io/badge/Security-Policy-2C3E50)](SECURITY.md)

Thistlewarden is a local web application for repeatable evaluation of OpenAI-compatible and Anthropic-compatible language models. Build reusable test suites, compare models under the same conditions, inspect quality and performance, and preserve the exact configuration used for each run.

![Thistlewarden dashboard showing evaluation, configuration, and test-material summaries](docs/manual/images/dashboard.png)

*Dashboard with synthetic demonstration data.*

## Status

**Version 0.4.0 is a beta release.** Use it for controlled evaluation and comparison, not as a public or multi-user service.

Thistlewarden is designed for one trusted user on one computer. It has no login system, user roles, or tenant isolation. Keep it bound to loopback (`127.0.0.1` or `::1`) and never expose it directly to a LAN or the internet. Read the [security policy](SECURITY.md) before installation.

Each run snapshots its selected test cases, model settings, Judge configuration, and quality gates. Later edits therefore affect future runs without rewriting completed evidence.

## Known limitations

- Supported deployment is one application process on one host, using local filesystem storage.
- Thistlewarden does not provide authentication, multi-user collaboration, or supported network deployment.

## Requirements

Choose one installation method:

- **Docker Compose v2** — recommended; or
- **Python 3.12 or newer** — for a native local installation.

SQLite, WAL files, exports, and backups must remain on a local filesystem. SMB, NFS, CIFS, shared volumes, and WSL network paths are unsupported.

You also need provider-approved credentials and the exact inference URL and model ID for each external model you intend to test. Provider and Judge calls may create charges.

## Docker Compose — recommended

1. Create local configuration:

   ```bash
   cp .env.example .env
   ```

   PowerShell: `Copy-Item .env.example .env`

2. Generate two independent secrets:

   ```bash
   python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

3. Put first value in `.env` as `ENCRYPTION_KEY` and second as `SECRET_KEY`. Never commit `.env`.
4. Build and start application:

   ```bash
   docker compose build
   docker compose up -d --wait
   ```

5. Open <http://127.0.0.1:5000>.

Compose publishes host loopback only, runs one non-root application process, and stores persistent data in named volume `thistlewarden-data`.

Useful commands:

```bash
docker compose logs -f evaluator
docker compose down                 # stop application; preserve data
docker compose down -v              # permanently delete named volume and all application data
```

> **Data-loss warning:** `docker compose down -v` permanently removes database and stored endpoint secrets. Export or back up required data first.

## Native installation

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
cp .env.example .env
# Generate independent SECRET_KEY and ENCRYPTION_KEY values as shown above.
python -m evaluator
```

Open <http://127.0.0.1:5000>. Keep terminal running while using application.


## URLs

- Application: <http://127.0.0.1:5000>
- User manual in application: <http://127.0.0.1:5000/manual/>

If `PORT` was changed, replace `5000` with configured local port. Do not replace loopback host with a network address.

## Configuration

Copy `.env.example` and change only values required for your installation. `.env` overrides process environment; GUI/database settings apply only when no environment value controls same setting.

| Variable | Default | Purpose |
|---|---:|---|
| `HOST` | `127.0.0.1` | Local bind address. Keep loopback on host. |
| `PORT` | `5000` | Local browser port. |
| `DATABASE_PATH` | `./data/evaluator.db` | Local SQLite database. |
| `EXPORT_PATH` | `./exports` | Local export directory. |
| `APP_PROCESS_COUNT` | `1` | Must remain exactly one. |
| `WORKERS_ENABLED` | `true` | Process queued evaluation jobs. |
| `RUN_CONCURRENCY` | `4` | Maximum simultaneous run jobs. |
| `MAX_RUN_CONCURRENCY` | `10` | Allowed concurrency ceiling. |
| `MAX_MODEL_PROFILES_PER_RUN` | `20` | Model-selection limit per run. |
| `RUN_RETENTION_DAYS` | `0` | `0` disables automatic cleanup. |

`CONTAINER_MODE=true` allows internal container bind to `0.0.0.0`; supported Compose configuration still publishes only `127.0.0.1` on host. See [configuration reference](docs/configuration.md) before changing runtime, storage, worker, or limit settings.

## Secrets and sensitive content

`ENCRYPTION_KEY` must be URL-safe base64 for exactly 32 random bytes. Endpoint API keys and secret headers are encrypted with AES-256-GCM. `SECRET_KEY` protects browser session and CSRF state. Generate keys independently, protect both, and preserve recovery copies.

Prompts, expected answers, model answers, Judge material, run notes, and exports are **not encrypted by Thistlewarden**. They remain plaintext in local SQLite or downloaded files. Protect computer, database, `.env`, backups, browser profile, and exports. Never place credentials or production secrets in test material.

## Automated local runs

Trusted local build agents can create, process, gate, summarize, and export runs without exposing web server:

```bash
thistlewarden-admin run lookup golden-sets
thistlewarden-admin run lookup models
thistlewarden-admin run start --golden-set <id> --model <id> --wait --output json
```

CLI and server cannot own same database simultaneously. Stop server before using `run wait` or `run start --wait`. See [command-line automation](docs/manual/runs-and-results.md#automating-runs-from-command-line) for exports, gate overrides, interruption behavior, and exit codes.

## Documentation

- [User manual](docs/manual/README.md) — first evaluation, daily workflows, and complete field guidance
- [Configuration reference](docs/configuration.md) — setting precedence, lifecycle, topology, and file formats
- [Security policy](SECURITY.md) — supported boundary, sensitive data, backups, and vulnerability reporting
- [0.4.0 beta release notes](docs/release-notes-0.4.0.md) — current changes and upgrade notes
- [Release guide](docs/release.md) — maintainer release procedure
- [Contributing](.github/CONTRIBUTING.md) — development and contribution rules

## License

Licensed under the [Apache License 2.0](LICENSE). Copyright 2026 Leo van der Meulen.
