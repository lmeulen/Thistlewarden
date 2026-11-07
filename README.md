# Thistlewarden

Thistlewarden bewaakt de kwaliteit en betrouwbaarheid van taalmodellen.

Lokale single-user webapp voor reproduceerbare evaluatie van OpenAI-compatible taalmodellen. Nederlandse GUI; Engelse code, API-velden, logs en technische documentatie.

## Status

MVP-implementatie **30/30 stappen voltooid**: golden sets, zes evaluatoren, meerdere modelprofielen, streamingmetrics, exacte kosten, duurzame queue/recovery, LLM-judge, quality gates, configuratie-uitwisseling, exports, retentie, Nederlandse GUI en security-/performancehardening.

## Vereisten

- Python 3.12+ of Docker Compose v2
- Lokaal filesystem voor SQLite, WAL, exports en backups
- Exact één applicatieproces; geen netwerkfilesystem of multiprocessdeployment

## Docker Compose — aanbevolen

```bash
cp .env.example .env
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
python -c "import secrets; print(secrets.token_urlsafe(48))"
# plaats uitvoer als ENCRYPTION_KEY en SECRET_KEY in .env
docker compose build
docker compose up -d --wait
```

PowerShell gebruikt `Copy-Item .env.example .env`; overige commando's blijven gelijk.

Open <http://127.0.0.1:5000>. Compose publiceert uitsluitend host-loopback, gebruikt één non-root containerproces en bewaart database/WAL/exports in named volume `thistlewarden-data`.

```bash
docker compose logs -f evaluator
docker compose down                 # data blijft bestaan
docker compose down -v              # VERWIJDERT ALLE DATA; alleen bewust uitvoeren
```

Bind mounts worden alleen ondersteund wanneer aantoonbaar lokaal filesystem. SMB/NFS/CIFS/WSL-netwerkpaden zijn niet ondersteund.

## Native installatie — POSIX

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
cp .env.example .env
# genereer/vervang SECRET_KEY en ENCRYPTION_KEY
python -m evaluator
```

## Native installatie — PowerShell

```powershell
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
Copy-Item .env.example .env
# genereer/vervang SECRET_KEY en ENCRYPTION_KEY
python -m evaluator
```

Productieserver is Waitress met één proces en vier HTTP-threads. Workerthreads worden begrensd door `RUN_CONCURRENCY`. Flask reloader blijft uit.

## URLs

- App: <http://127.0.0.1:5000>
- Instellingen: <http://127.0.0.1:5000/instellingen>
- Endpoints/modellen: <http://127.0.0.1:5000/endpoints>
- Golden sets: <http://127.0.0.1:5000/golden-sets>
- Judges: <http://127.0.0.1:5000/judges>
- Configuratie: <http://127.0.0.1:5000/configuratie>
- Runs: <http://127.0.0.1:5000/runs>
- Liveness: <http://127.0.0.1:5000/health>
- Readiness: <http://127.0.0.1:5000/ready>
- OpenAPI 3.1: <http://127.0.0.1:5000/api/v1/openapi.json>

## Configuratie

`.env` gebruikt bewust `python-dotenv override=True`: bestand overschrijft procesenvironment. Prioriteit: `.env > GUI/database > default`. Actieve environmentwaarden zijn read-only in GUI/API.

Belangrijkste velden:

| Variabele | Default | Betekenis |
|---|---:|---|
| `HOST` | `127.0.0.1` | Host-native alleen loopback |
| `PORT` | `5000` | Lokale poort |
| `DATABASE_PATH` | `./data/evaluator.db` | Lokale SQLite-database |
| `EXPORT_PATH` | `./exports` | Exportpad |
| `APP_PROCESS_COUNT` | `1` | Moet exact één zijn |
| `WORKERS_ENABLED` | `true` | Production workerthreads |
| `RUN_CONCURRENCY` | `4` | Gelijktijdige jobs |
| `MAX_RUN_CONCURRENCY` | `10` | Bovengrens |
| `MAX_MODEL_PROFILES_PER_RUN` | `20` | Modelgrens per run |
| `RUN_RETENTION_DAYS` | `0` | Nul schakelt cleanup uit |

Alle limieten staan in `.env.example`. `CONTAINER_MODE=true` is alleen voor containerentrypoint en staat `HOST=0.0.0.0` intern toe; Compose bindt extern alsnog `127.0.0.1`.

## Secrets en gevoelige inhoud

`ENCRYPTION_KEY` is URL-safe base64 van exact 32 random bytes. Endpointsecrets gebruiken AES-256-GCM. Gewone exports, API-responses en logs bevatten nooit secretwaarden. Prompts en antwoorden blijven volgens MVP plaintext in lokale SQLite en runexports: bescherm database, backups en downloads.

## Databasebeheer

```bash
python -m evaluator.cli migrate
python -m evaluator.cli backup ./backups/manual.db
python -m evaluator.cli restore ./backups/manual.db ./data/restored.db
```

Restore weigert bestaand doel. Normale startup houdt process-lifetime instance lock, migreert, herstelt verlopen leases, start workers en opent daarna HTTP-server. Bestaande schema-upgrade maakt eerst consistente backup.

## Testen en release

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest -q tests/unit tests/integration
```

Offline Docker E2E gebruikt `compose.e2e.yaml`, `evaluator.mock_provider`, `examples/release-configuration.json` en `scripts/e2e_release.py`. Zie `docs/release.md`.

## Documentatie

- `specification.md` — requirements
- `implementation.md` — 30-stappenplan en status
- `docs/configuration.md` — settings en schema's
- `docs/operations.md` — startup, backup, restore, storage en recovery
- `docs/release.md` — releasegate, Docker E2E en rollback
- `SECURITY.md` — threat model en securitybeleid
- `hardening-report.md` — stap-29-metingen en restrisico's
