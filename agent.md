# Agent guide — Thistlewarden

Dit bestand is primaire overdracht voor iedere nieuwe coding-agent. Lees dit volledig vóór wijzigingen. Controleer daarna relevante broncode en tests; documentatie kan achterlopen op code.

## 1. Opdracht en productgrens

Thistlewarden is een lokale single-user webapp voor reproduceerbare evaluatie van OpenAI-compatible taalmodellen. Gebruiker beheert endpoints, modelprofielen, golden sets, judgeprofielen en runs. Runs vergelijken meerdere targetmodellen op vaste cases, streamingmetrics, targetkosten en quality gates.

Niet veranderen zonder expliciete productbeslissing:

- Nederlandse GUI en gebruikersmeldingen.
- Engelse code, API-velden, logs en technische documentatie.
- Python 3.12+, Flask, SQLite, Pydantic en lichte browser-JavaScript.
- Server-rendered HTML/SVG; geen zware frontendstack of externe chartdependency.
- Eén OS-host, één applicatieproces, lokaal filesystem, begrensde threads.
- Geen authenticatie: hostpublicatie blijft loopback-only.
- Targetcalls zijn SSE-streaming; judgecalls zijn non-streaming.
- OpenAI-compatible calls gebeuren uitsluitend server-side.
- Geld, prijzen, scores, gewichten en evaluatorgetallen gebruiken `Decimal`, nooit `float` voor domeinberekeningen.

Hoofddocumenten:

- `specification.md`: requirements en invarianten.
- `implementation.md`: oorspronkelijk 30-stappenplan.
- `README.md`: installatie en gebruikersingang.
- `SECURITY.md`: ondersteunde threat boundary.
- `docs/configuration.md`: configuratiecontracten.
- `docs/operations.md`: lifecycle, backup en recovery.
- `docs/release.md`: releasegate en offline E2E.
- `docs/logging-plan.md`: loggingbeleid.
- `reviews/combined_results.md`: actuele geconsolideerde review en releaseblokkers.
- `hardening-report.md`: hardeningbewijs en restrisico's.

## 2. Huidige status

Oorspronkelijke MVP-functionaliteit is geïmplementeerd: zes evaluatoren, providerintegratie, centrale limiter, kosten, queue/leases/recovery, immutable case/model/judgesnapshots, gates, configuratie-import/export, runexports, retentie, Nederlandse GUI en securityhardening.

**Release is momenteel no-go.** Compose-hostvalidatie is hersteld: bootstrap- en operationele settings staan `HOST=0.0.0.0` uitsluitend toe bij `CONTAINER_MODE=true`, terwijl Compose extern loopback-only publiceert. Los minimaal deze resterende bevestigde blockers op vóór stabiele release:

1. **High — migratie 9 is niet atomair.** `_migration_9()` gebruikt `sqlite3.executescript()`, dat impliciet commit en transactionele garanties breekt. Zie `src/evaluator/migrations.py`.
2. **High — runs zijn niet volledig snapshotgedreven.** Cases, modellen, judge en gates zijn gesnapshot, maar `StreamingTargetCaller` en `JudgeCaller` laden actuele endpointconfiguratie en headers opnieuw. Endpointwijziging of deactivatie na queueing kan runuitvoering veranderen.

Belangrijke open punten daarna:

- Streamingclient vertaalt netwerk-/timeoutfouten naar resultaten, waardoor executor-retries kunnen worden omzeild.
- Runannulering en shutdown breken actieve providercalls niet af.
- GUI-instellingen kunnen opgeslagen lijken terwijl constructorgebonden runtimecomponenten oude waarden blijven gebruiken.
- `max_model_answer_chars` is niet volledig aangesloten; totale jobs per run zijn onvoldoende begrensd.
- DNS-validatie heeft SSRF/DNS-TOCTOU-restrisico omdat connectie-IP niet gepind wordt.
- Endpoint- en secretupdates gebruiken meerdere transacties.
- `/ready` controleert SQLite, niet workerbeschikbaarheid.
- Future-schema's worden niet volledig fail-closed geweigerd.
- CSV-output neutraliseert spreadsheetformules niet.
- Judgeveld `prompt_template` wordt opgeslagen en gesnapshot, maar `build_judge_prompt()` gebruikt het niet.
- Model-prijzen zijn nulplaceholders; presenteer deze niet als financiële schatting.

Recente volledige suite: 383 tests groen op 2026-11-07 via `venv/Scripts/python.exe -m pytest` (Python 3.12.10, pytest 8.4.2). Ruff check, Ruff format check en strict Mypy zijn groen. Leakscan en Docker E2E zijn daarbij niet gedraaid.

Omgevingsbeperkingen van huidige werkmap kunnen veranderen, maar waren laatst:

- Native interpreter: `venv/Scripts/python.exe` (Python 3.12.10, editable install).
- Docker niet beschikbaar in huidige WSL-host; CI bevat Dockergate.
- Werkmap was geen Git-repository. Vertrouw niet op `git diff/status` zonder eerst te controleren.

## 3. Niet-onderhandelbare invarianten

### Runtime en tijd

- Exact één proces. Geen Gunicorn-multiprocess, meerdere replicas of netwerkfilesystem.
- `python -m evaluator` neemt process-lifetime `InstanceLock` vóór initialisatie.
- Waitress gebruikt één proces en vier HTTP-threads; workerthreads volgen `RUN_CONCURRENCY`.
- SQLite, WAL, backups en exports moeten op lokaal filesystem staan.
- Persistente timestamps en leases: timezone-aware UTC via `evaluator.time`.
- Latency, backoff en wachtduren: monotone klok.
- Queuewrites moeten fenced blijven op lease-owner, generation, expiry en actieve runstatus.
- Actieve providercalls kunnen nu niet hard worden afgebroken; timeouts en lease recovery begrenzen schade.

### Configuratie

- Prioriteit: `.env > GUI/database > default`.
- `.env` wordt bewust geladen met `python-dotenv override=True`; bestand overschrijft procesenvironment.
- Environmentgestuurde settings zijn read-only in GUI/API.
- Bootstrapsettings en operationele settings zijn nu twee modellen; voorkom verdere duplicatie en herstel hostinconsistentie.
- Classificeer nieuwe settings expliciet als bootstrap, restart-required of werkelijk live.

### Secrets en gevoelige data

- `ENCRYPTION_KEY`: URL-safe base64 van exact 32 random bytes.
- Endpointsecrets: AES-256-GCM, random nonce, recordgebonden AAD en key versioning.
- Nooit secretwaarden in logs, exports, API-/frontendresponses, snapshots, tests, fixtures of documentatie.
- Configuratie-export bevat geen secrets; secretvelden zijn leeg/null.
- Prompts, expected answers, modelantwoorden en judgedetails staan bewust plaintext in SQLite en runexports. Behandel database, WAL, backups en exports als gevoelig.
- Lees of toon lokale `.env`, providercredentials of productie-inhoud niet tenzij gebruiker dit expliciet en veilig vraagt.

### Netwerk en browser

- Provider-HTTPS standaard verplicht. HTTP alleen localhost of expliciet vertrouwd intern endpoint met bevestigde waarschuwing.
- Providerclients: `follow_redirects=False`, `trust_env=False`, harde responslimieten.
- Eén centrale thread-safe limiter delen target-, judge-, discovery-, verbindingstest- en modeltestcalls.
- Host-/Originvalidatie, CSP-nonces en sessiegebonden CSRF blijven actief.
- `Origin: null` alleen voor GUI-mutaties met geldig sessie-CSRF; nooit voor `/api/v1`.
- App niet naar LAN/internet exposen; authenticatie en tenantisolatie bestaan niet.
- Geen inline JavaScript-handlers. Gebruik nonce-script en eventlisteners.

### Logging

- Eén `StreamHandler` naar `stderr`.
- Gebruik `log_event(...)` en allowlisted velden.
- `INFO`: lifecycle, semantische gebruikersacties, runvoortgang; geen normale GET-/pollingruis.
- `DEBUG` mag technische metadata tonen, nooit prompts, antwoorden, expected answers, bodies, SSE-chunks, cookies, CSRF, secrets of geheime headers.
- Ruwe providerfouten nooit loggen, opslaan of exporteren.
- Handmatige modeltestdiagnostiek is response-lokaal, maximaal 32 KiB, alleen tekst/JSON, HTML-escaped en met bekende headerwaarden geredigeerd. Alleen `ModelProfileTester.test()` gebruikt `diagnostic_mode=True`.

### Kosten en usage

- Zichtbare run-/modelkosten en cost gates zijn uitsluitend targetkosten.
- `judge_cost` blijft alleen auditdata in opslag/export; nooit optellen bij totalen, ranking of gates.
- `total_cost` is strikt onbekend als niet alle targetkosten bekend zijn.
- `known_total_cost` telt bekende targetkosten; toon bekende teller apart.
- Targetruntime normaliseert ontbrekende usagecomponenten intern naar nul voor prijsberekening, zodat fixed request cost blijft tellen.
- Niet-gerapporteerde providertokens blijven in opslag `NULL`.
- Generieke kosten-API blijft strikt bij ontbrekende usage; verruim deze niet stilzwijgend.

## 4. Architectuurkaart

### Composition en lifecycle

- `src/evaluator/server.py`: instance lock, appbouw, workers, Waitress en shutdown.
- `src/evaluator/app.py`: composition root; bouwt repositories/services/clients/queue/workers en registreert blueprints.
- `src/evaluator/config.py`: pre-database bootstrapsettings.
- `src/evaluator/settings.py`: registry, DB/env/default-resolutie en operationele settings.
- `src/evaluator/database.py`: SQLite factory, pragmas en transacties.
- `src/evaluator/migrations.py`: schema v12, backups en migratiemanager.
- `src/evaluator/runtime.py`: process instance lock.

Startupvolgorde: settings laden → instance lock → migreren → services bouwen → verlopen leases zichtbaar maken → workers starten → Waitress starten. Shutdown: HTTP-server sluiten → workers stoppen → limiter sluiten → lock vrijgeven.

### Weblagen

- `src/evaluator/gui.py`: Nederlandse GUI-routes en form parsing.
- `src/evaluator/templates/`: server-rendered Jinja.
- `src/evaluator/static/app.css`: layout en responsieve styling.
- `src/evaluator/api/routes.py`: `/api/v1` JSON-routes.
- `src/evaluator/api/openapi.py`: OpenAPI 3.1-contract.
- `src/evaluator/api/handlers.py`: veilige foutmapping.
- `src/evaluator/web_security.py`: host/origin/CSRF/CSP/securityheaders.

GUI en API horen dezelfde services en Pydantic-validatie te gebruiken. Nieuwe mutatie vereist GUI-CSRF; automation-API behoudt eigen originbeleid. Werk OpenAPI, GUI, config import/export en tests samen bij wanneer veldcontract wijzigt.

### Domeinen

- `endpoints.py`, `secrets.py`: providerendpoint, headers en versleutelde secrets.
- `models.py`: modelprofielen, inferencebindings en prijzen.
- `golden_sets.py`, `golden_set_io.py`: set/case CRUD, evaluatorconfig, import/export.
- `judges.py`: judgeprofielen, rubric, promptcontract en strikte parser.
- `evaluators/`: deterministic evaluatorregistry.
- `runs.py`: runcreatie, snapshots, queue, leases, resultaten en status.
- `workers.py`: claim → heartbeat → target → evaluator/judge → fenced commit → gates.
- `worker_runtime.py`: echte target/judge adapters en workerthreads.
- `streaming.py`: SSE-targetclient en usage/TTFT/TPS.
- `provider.py`: non-streaming judgeclient en headers.
- `rate_limits.py`: centrale limiter en retryexecutor.
- `costs.py`: exacte kostenberekening.
- `aggregates.py`, `gates.py`: run/modelaggregaten en quality gates.
- `run_views.py`, `run_exports.py`: GUI-projecties en JSON/CSV/HTML-export.
- `config_io.py`: secretvrije configuratiebundle.

### Runflow

1. `RunQueue.create_run()` valideert actieve set/modellen/judge.
2. Cases, modellen, judge en gates worden in één transactie gesnapshot.
3. Alleen gekozen targetmodellen krijgen jobs; judge-only model kan extra snapshot krijgen.
4. Worker claimt één job met owner/generation/expiry.
5. Heartbeat verlengt lease; stale worker mag niet committen.
6. Targetcall streamt SSE en verzamelt content, latency, TTFT, TPS en usage.
7. Deterministische evaluator of LLM-judge produceert `EvaluationResult`.
8. Resultaat en metrics worden fenced opgeslagen.
9. Runstatus wordt ververst; terminale run evalueert gates.

Behoud onderscheid:

- `failed` evaluatorresultaat = technisch correcte uitvoering, inhoud niet geslaagd.
- `error` resultaat/job `failed` = technische pipelinefout.
- `completed_with_errors` = terminale run met sommige technische fouten.

## 5. Evaluatorcontracten

Methoden:

- `exact_match`
- `contains`
- `regex`
- `numeric`
- `json_schema`
- `llm_judge`

Golden-setconfig wordt bij write én database-read door Pydantic gevalideerd. Regex behoudt afdwingbare timeout. Evaluatoren retourneren `Decimal`-score, boolean `passed`, beperkte reason en veilige metadata.

### JSON Schema

`JsonSchemaEvaluator`:

1. verwacht standalone JSON;
2. verwijdert alleen optionele geldige Markdownfence wanneer geconfigureerd;
3. valideert Draft 2020-12;
4. vergelijkt volledige parsed value wanneer `expected_value` geconfigureerd is;
5. scoort binair 0/1: één overtreding faalt hele case.

Recente model-analyse liet vooral geldige JSON met contractmismatch zien, niet evaluatorcrashes. Testsetproblemen omvatten impliciete veldnamen/types, taaltegenstrijdigheden (`positief` in prompt versus `positive` als `const`), string versus array, string versus integer en verboden codefences. Bij onderhoud van JSON-cases:

- zet exacte veldnamen, types, arrays en constante waarden in prompt;
- gebruik `const`/`expected_value` alleen als exacte serialisatie requirement is;
- kies expliciet of codefences onderdeel van test zijn;
- verander evaluator niet om ambigue golden cases kunstmatig te laten slagen.

### LLM judge

`build_judge_prompt()` bevat expliciet outputcontract, exacte criterion-ID's/volgorde, prompt-injectiongrens en verbod op extra tekst. `parse_judge_output()` is fail-closed:

- exact `score`, `passed`, `reason`, `criteria`;
- elk criterion exact `id`, `score`, `reason`;
- exacte criterionset;
- gewogen score wordt server-side opnieuw berekend;
- threshold bepaalt werkelijk `passed`.

Historische `JudgeParseError`-runs kwamen door ontbrekend outputcontract en worden niet retroactief aangepast. Herstart run voor nieuwe prompt. Judge doet maximaal twee output-/parsepogingen. Beslis expliciet hoe opgeslagen `prompt_template` toegepast wordt; momenteel genegeerd. Voeg vóór implementatie template-injection-, escaping-, versie- en backward-compatibilitytests toe.

## 6. Provider- en modelcontracten

Modelparameters zijn optioneel; leeg betekent **niet verzenden**. Nieuwe profielen hebben `temperature=None`.

`compose_inference_parameters()`:

- beschermt `model`, `messages`, `stream`, `stream_options` en vaste bindings;
- ondersteunt configureerbaar `max_tokens_field_path` inclusief nested pad;
- ondersteunt vlak/nested reasoningpad en optionele verzending van `off`;
- weigert conflicten met `custom_parameters`;
- laat geen NaN/infinite JSON toe.

Modeltest gebruikt uitsluitend opgeslagen profiel via productie-streamingpad met vaste veilige prompt. Niet-opgeslagen formulierwijzigingen mogen modeltest niet beïnvloeden.

Targetrequest voegt `stream_options.include_usage=true` toe. Stream geldt als compleet bij `[DONE]` of geldige finish reason volgens streamingcontract. Bewaak harde byte-/antwoordlimieten bij wijzigingen.

Endpointheaders kunnen plaintext of secretref zijn. Secretwaarden worden pas server-side opgelost. Discovery, target, judge en tests moeten allemaal centrale limiter gebruiken.

## 7. Database en migraties

Actueel schema: **v12** (`add_model_parameter_bindings`). Tabellen omvatten settings, secrets, endpoints/headers, models, costs, golden sets/items, judges, runs/snapshots/jobs/attempts/results en gate results.

Migratieregels:

- Alleen forward migrations; geen down migrations.
- Voeg nieuwe migration met volgende opeenvolgende versie toe.
- Gebruik afzonderlijke `connection.execute()`-statements binnen bestaande `EXCLUSIVE` transactie; geen `executescript()`.
- Nooit bestaande historische migration wijzigen nadat release deze kan hebben toegepast, behalve gerichte reparatiestrategie met compatibiliteitstest.
- Nieuwe/gewijzigde brede `INSERT` gebruikt expliciete kolomnamen; vermijd positionele koppeling.
- Test leeg schema, upgrade vanaf historische versie, foutinjectie/rollback, backupintegriteit en future-schema failure.
- Bestaande upgrade maakt unieke SQLite-onlinebackup; verse database heeft geen pre-upgradebackup.
- Restore schrijft altijd naar nieuw doelpad.

SQLite-instellingen (`database.py`) zoals WAL, foreign keys, `synchronous=FULL` en busy timeout zijn correctnessonderdeel, geen vrijblijvende tuning.

## 8. Golden sets

Golden-setduplicatie kopieert atomair metadata, cases, volgorde, judgeoverride en quality gates. Bulkdelete valideert UUID's en setlidmaatschap vooraf, verwijdert transactioneel en normaliseert `sort_order`. Historische runrefs blijven beschermd.

## 9. Wijzigingswerkwijze voor agents

1. Lees deze gids plus relevante requirements/docs/code/tests volledig.
2. Reproduceer bug met kleinste veilige test of read-only databasequery. Toon geen gevoelige inhoud.
3. Bepaal invarianten en aangrenzende contracten vóór edit.
4. Schrijf regressietest die vóór fix faalt waar praktisch.
5. Maak kleinste coherente wijziging in domeinservice; dupliceer logica niet in GUI/API.
6. Werk Pydanticmodel, persistence, migration, config exchange, GUI, API/OpenAPI en tests samen bij indien contract verandert.
7. Draai gerichte formatter/lint/type/testchecks.
8. Draai volledige quality gate voor afronding.
9. Scan diff/werkmap op secrets en payloadlekken.
10. Werk documentatie en deze gids bij wanneer architectuur, status of risico verandert.

Geen brede refactor tegelijk met bugfix tenzij noodzakelijk. Behoud transactieboundaries en fencing. Gebruik dependency injection voor klok, sleeper, random, transport en repositories in tests. Concurrencytests liefst event/barrier-gebaseerd, niet sleep-gebaseerd.

Bij databaseonderzoek:

- maak eerst backup bij mutaties;
- gebruik parameterized SQL;
- doe diagnose read-only waar mogelijk;
- onderscheid historische data van actuele code;
- wijzig oude runresultaten nooit om nieuwe logica te simuleren.

## 10. Test- en kwaliteitscommando's

Gebruik voor alle Python-commando's uitsluitend de interpreter uit `venv/`; gebruik geen globale Python. In deze Windows/WSL-werkmap is dat `./venv/Scripts/python.exe`. Op POSIX is dat `./venv/bin/python`.

Windows/WSL-werkmap:

```bash
./venv/Scripts/python.exe -m ruff check .
./venv/Scripts/python.exe -m ruff format --check .
./venv/Scripts/python.exe -m mypy src
./venv/Scripts/python.exe -m pytest -q tests/unit tests/integration
```

POSIX-venv gebruikt overeenkomstig `./venv/bin/python -m ...`.

Gerichte tests volgen modulestructuur, bijvoorbeeld:

```bash
./venv/Scripts/python.exe -m pytest -q tests/unit/test_judges.py
./venv/Scripts/python.exe -m pytest -q tests/unit/test_json_schema_evaluator.py
./venv/Scripts/python.exe -m pytest -q tests/integration/test_worker_pipeline.py
./venv/Scripts/python.exe -m pytest -q tests/integration/test_run_queue.py
./venv/Scripts/python.exe -m pytest -q tests/integration/test_migrations.py
./venv/Scripts/python.exe -m pytest -q tests/integration/test_gui_runs.py
```

Na Pythonwijziging minimaal: Ruff check, Ruff format check, strict Mypy en relevante tests. Voor release: volledige suite plus offline Docker E2E uit `docs/release.md`.

Dockerreleasegate wanneer Docker beschikbaar:

```bash
docker compose -f compose.yaml -f compose.e2e.yaml config --quiet
docker compose -f compose.yaml -f compose.e2e.yaml build --pull
docker compose -f compose.yaml -f compose.e2e.yaml up -d --wait
python scripts/e2e_release.py
docker compose -f compose.yaml -f compose.e2e.yaml logs --no-color > compose.log
! grep -F "release-fixture-secret" compose.log
docker compose -f compose.yaml -f compose.e2e.yaml down -v
```

E2E gebruikt lokale `mock_provider`, alle zes evaluatoren, twee modellen, streaming, judge, usage, kosten, gates en exports. Werkelijke Dockerstart is verplicht bewijs; statische tests vervangen dit niet.

Leakscan moet minstens uitsluiten:

- echte API keys of `.env`-waarden in tracked artefacten;
- secretwaarden in exports/responses/logs/snapshots;
- prompts, candidates, expected answers, requestbodies of SSE-content in logs;
- fixturesecret in E2E-logs.

## 11. Definitie van klaar

Wijziging is klaar wanneer:

- gedrag en invarianten expliciet zijn;
- regressietests bestaan voor bug/contract;
- GUI/API/config/exportpaden consistent zijn waar relevant;
- transacties, fencing, Decimal en UTC/monotone tijd correct blijven;
- security- en loggingbeleid niet verzwakt is;
- Ruff, format, strict Mypy en relevante tests groen zijn;
- volledige suite groen is voor brede wijziging/release;
- Docker E2E groen is voor deployment/releasewijziging;
- geen secrets of gevoelige payloads lekken;
- docs, OpenAPI en `agent.md` actuele werkelijkheid beschrijven.

Rapporteer altijd exact welke checks gedraaid zijn en welke niet konden draaien. Claim geen release readiness zolang blockers uit §2 openstaan.
