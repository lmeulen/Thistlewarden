# Logging plan

Status: implemented. Core contract, app-wide correlation, semantic mutation events, worker/provider diagnostics, CLI lifecycle events, and leak-focused tests are active. Optional future JSON/file logging remains deferred as described below.

## 1. Doel en uitgangspunten

Logging moet lokaal beheer ondersteunen zonder database of GUI te vervangen.

- `INFO`: chronologisch overzicht van lifecycle, gebruikersmutaties en runvoortgang op hoofdlijnen.
- `DEBUG`: technische stappen binnen dezelfde actie, nuttig voor diagnose.
- `WARNING`: verwachte maar afwijkende toestand die aandacht kan vragen.
- `ERROR`: actie of job mislukt door applicatie-, opslag- of providerfout.
- `CRITICAL`: proces kan niet veilig starten of doorgaan.

Logs zijn operationele metadata. Prompts, antwoorden, expected answers, rubrics, providerresponsebody's, API-keys, secret headers, cookies, CSRF-tokens en volledige import/exportinhoud worden nooit gelogd.

## 2. Uitvoer en configuratie

### Eerste implementatie

Behoud één `StreamHandler` naar `stderr`. Dit past bij native terminals, systemd en containers.

```text
LOG_LEVEL=INFO
```

- Native zichtbaar in actieve terminal; redirect/Tee voor bestand.
- Docker zichtbaar via `docker compose logs -f evaluator`.
- Geen ingebouwde logfile of rotatie in fase 1; procesmanager/containerplatform beheert retentie.

Latere optionele uitbreiding:

```text
LOG_FORMAT=text|json
LOG_FILE=<local path or empty>
LOG_FILE_MAX_BYTES=10485760
LOG_FILE_BACKUP_COUNT=5
```

Bestandlogging gebruikt alleen `RotatingFileHandler`, weigert netwerkfilesystem en heeft dezelfde redactionfilter.

## 3. Vast eventcontract

Elke regel bevat bestaande basisvelden:

```text
timestamp level component request_id run_id case_id execution_id message
```

`message` begint met stabiele Engelse `snake_case` eventnaam. Daarna alleen allowlisted `key=value`-velden. Eventnamen en velden zijn API-achtig stabiel; menselijke Nederlandse GUI-tekst wordt niet gebruikt als eventnaam.

Aanvullende veilige velden:

- request: `method`, route-template, `status`, `duration_ms`, `interface=gui|api`;
- object: UUID, technisch type, niet-gevoelige naam indien nodig;
- import: modus, preview/commit, objectaantallen, conflict- en missing-secret-aantallen;
- run: status, jobs total/done/error, modelaantal, caseaantal, gate-status;
- provider: endpoint-ID, model-ID, poging, statuscategorie, latency, TTFT, tokenaantallen;
- worker: lease generation, resultaatstatus, evaluator-methode, foutcode.

Niet loggen:

- URL querystring; routeparameter mag alleen UUID zijn;
- request-/responsebody, multipartbestandsnaam of inhoud;
- endpointheaders of providerheaders;
- exceptiontekst van provider of parser wanneer die bodydata kan bevatten;
- gebruikersnotitie, prompt, kandidaatantwoord, context of schema-inhoud;
- lokale API-key, encryptiesleutel, sessiecookie of token.

## 4. Correlatie

1. Ken **iedere** GUI- en API-request een `request_id` toe, niet alleen `/api/v1`.
2. Accepteer geen willekeurige externe request-ID als logveld; genereer server-side UUID.
3. Bind routebekende `run_id` met `log_context`.
4. Bind workerclaim aan `run_id`, `case_id` en nieuwe willekeurige `execution_id`.
5. Judgecall houdt dezelfde workercontext.
6. Providerretry behoudt context en voegt alleen `attempt` toe.

Responseheader `X-Request-ID` wordt voor GUI en API geretourneerd. Foutpagina toont request-ID zodat gebruiker één actie kan terugvinden.

## 5. INFO-eventcatalogus

INFO toont betekenisvolle veranderingen, niet elke GET of pollingcall.

### Proces en beheer

| Event | Wanneer | Kernvelden |
|---|---|---|
| `application_initialized` | database/migraties/config gereed | host, port, workers_enabled, database |
| `recovery_completed` | startup recovery uitgevoerd | recovered_runs, recovered_jobs |
| `worker_runtime_started` | workers gestart | concurrency |
| `http_server_listening` | server gereed | host, port |
| `shutdown_started` / `shutdown_completed` | gecontroleerde stop | geen gevoelige velden |
| `backup_completed` / `restore_completed` | CLI-actie gereed | doelpad, bytes/duration |
| `retention_cleanup_completed` | retentieactie | deleted_runs |

### Gebruikersacties via GUI en API

Elke geslaagde mutatie krijgt precies één semantisch INFO-event in servicelaag, zodat GUI en API hetzelfde event leveren.

| Gebied | Events |
|---|---|
| Settings | `setting_updated` met settingnaam en bron, nooit secretwaarde |
| Endpoints | `endpoint_created`, `endpoint_updated`, `endpoint_deleted`, `endpoint_connection_tested` |
| Discovery | `model_discovery_completed`, `discovered_models_imported` |
| Models | `model_profile_created`, `model_profile_updated`, `model_profile_deleted` |
| Golden sets | `golden_set_created`, `golden_set_updated`, `golden_set_deleted`, `golden_set_import_previewed`, `golden_set_imported`, `golden_set_exported` |
| Cases | `golden_item_created`, `golden_item_updated`, `golden_item_deleted` |
| Judges | `judge_profile_created`, `judge_profile_updated`, `judge_profile_deleted`, `golden_set_judge_changed` |
| Gates | `quality_gates_updated` met alleen geconfigureerde gate-namen |
| Config exchange | `configuration_exported`, `configuration_import_previewed`, `configuration_imported` met aantallen |
| Runs | `run_created`, `run_cancel_requested`, `run_failures_rerun`, `run_deleted`, `run_exported` |

Leesacties (`GET` pagina, statuspolling, lijsten) komen niet op INFO. Daardoor blijft INFO leesbaar als auditachtig hoofdoverzicht. Authenticatie bestaat niet, dus log geen verzonnen gebruiker; gebruik `actor=local_gui` of `actor=api`.

### Runuitvoering

| Event | Wanneer | Kernvelden |
|---|---|---|
| `run_started` | eerste job claimt pending run | jobs_total, target_models |
| `run_progress` | maximaal per 10 afgeronde jobs of 10% | done, total, errors |
| `run_completed` | terminale run | status, passed, failed, errors, gate_status |
| `run_cancelled` | cancellation effectief | done, total |
| `job_failed` | testcase eindigt als error | error_code, evaluator_method |

Succes per testcase staat niet op INFO; honderd cases zouden hoofdlijnen verdringen.

## 6. DEBUG-eventcatalogus

DEBUG beschrijft stappen, maar nooit inhoud.

### HTTP

- `request_started`: method, route-template, interface, content_length.
- `request_completed`: status, duration_ms.
- `csrf_validated`, `origin_validated`, `host_validated`: alleen resultaat/beleid, geen token/headerwaarde.
- `payload_validated`: modelnaam en objectaantal.

Health/readiness en `/runs/<id>/status` mogen op DEBUG maar niet INFO.

### Database en transacties

- `transaction_started`, `transaction_committed`, `transaction_rolled_back`: operationele naam en duration.
- Geen SQL-parameters. Raw SQL alleen indien statisch en expliciet veilig; standaard niet loggen.
- `migration_started`, `migration_applied`, `migration_completed`: versienummers.

### Queue en workers

- `job_claim_started`, `job_claimed`: job-ID, lease generation.
- `lease_heartbeat`, `lease_recovered`, `lease_lost`.
- `case_snapshot_loaded`, `model_snapshot_loaded`: IDs, methode/model-ID.
- `target_call_started`, `target_stream_completed`: endpoint/model, duration, TTFT, bytes, finish reason, usagevelden.
- `evaluation_started`, `evaluation_completed`: methode, score, passed.
- `judge_call_started`, `judge_parse_started`, `judge_parse_retry`, `judge_call_completed`.
- `job_result_committed`, `run_status_refreshed`, `quality_gates_evaluated`.

Heartbeatlogging kan luid zijn. Log alleen eerste heartbeat en daarna maximaal één per 30 seconden per job, of maak het `DEBUG` met sampling.

### Provider en limiter

- `destination_validated`: scheme en classificatie, niet volledige URL.
- `rate_limit_wait_started/completed`: endpoint-ID en wait_ms.
- `provider_attempt_started/completed`: poging, statuscodecategorie, duration.
- `provider_retry_scheduled`: foutcategorie en backoff_ms.
- `sse_stream_opened/completed`: event_count, byte_count, finish_reason, usage_present.

Geen SSE-chunks, JSON-body of responseheaders loggen.

### Import/export

- parse-, schema-, referentie- en planstappen met objectaantallen;
- geen payloadfragmenten;
- bij validatiefout alleen veilig fouttype, veldpad en row number wanneer dit geen content bevat.

## 7. WARNING-, ERROR- en CRITICAL-beleid

### WARNING

- providerretry of rate-limitbackoff;
- expired lease/recovery;
- ontbrekend endpointsecret blokkeert call;
- partial/invalid stream afgewezen;
- run voltooid met fouten;
- gate-status failed/indeterminate;
- shutdown timeout;
- importpreview met remaps/conflicten.

### ERROR

Gebruik `logger.exception` alleen voor interne onverwachte exceptions. Voor bekende fouten log event plus veilige `error_code` en `exception_type`, niet `str(exception)` als provider-/payloadinhoud kan lekken.

- definitieve providercallfout;
- jobcommit/queuefencing mislukt;
- databaseoperatie mislukt;
- onverwachte requestfout;
- worker iteration failure.

### CRITICAL

- instance lock niet verkrijgbaar;
- database migratie/integriteitscontrole mislukt;
- ongeldige bootstrapconfig;
- server kan niet binden/starten.

Startupfouten vóór `configure_logging()` krijgen een minimale bootstraphandler op `stderr`.

## 8. Implementatiearchitectuur

### Centrale eventhelper

Voeg `log_event(logger, level, event, **safe_fields)` toe in `logging_config.py`.

- allowlist veldnamen en primitieve waarden;
- reject/redact verdachte namen (`secret`, `token`, `key`, `authorization`, `cookie`, `prompt`, `answer`, `body`, `content`);
- begrens elke waarde en hele regel;
- sorteer velden deterministisch;
- converteer UUID/Decimal veilig;
- ondersteun tekst- en toekomstige JSON-formatter vanuit hetzelfde record.

Geen verspreide f-strings met willekeurige objecten.

### Requestinstrumentatie

Breid `api/handlers.py` uit naar applicatiebrede requestcorrelatie:

- `before_request`: request-ID, monotone starttijd, DEBUG request-start;
- `after_request`: duration, responseheader, DEBUG request-end;
- semantische mutatielogs blijven servicelaagverantwoordelijkheid.

### Servicelaag

Plaats INFO-events pas na succesvolle transactionele commit. Previewevents na volledige validatie. Fouten worden op grenslaag één keer gelogd om dubbele regels te voorkomen.

### Workerlaag

`WorkerPipeline.run_once` bindt context en emit DEBUG-stappen. `RunQueue.refresh_run_status` emit alleen transition-events, niet bij ongewijzigde status. Progress wordt gethrottled via databasewaarden, niet per threadgeheugen alleen.

## 9. Prestatie- en volumebudget

Voor baseline van 100 cases, één targetmodel en judge:

- INFO: minder dan circa 30 regels exclusief joberrors;
- DEBUG: circa 8–20 regels per job, zonder chunks/heartbeatspam;
- logcalls mogen providerstream niet blokkeren met netwerk-I/O;
- tekstformattering wordt door logging-level guard overgeslagen waar velden duur zijn;
- maximaal 8 KiB per logregel.

## 10. Teststrategie

### Unit

- INFO/DEBUG-filtering;
- eventveldallowlist en lengtebegrenzing;
- redactie van secrets in message, args en extra fields;
- verbod op prompt/body/header/cookievelden;
- context bind/reset over threads;
- deterministische text- en JSON-output;
- exceptionlogging toont type maar geen gevoelige tekst.

### Integratie

- GUI-mutatie en equivalente API-mutatie leveren zelfde semantische event;
- GET/statuspolling levert geen INFO;
- request-ID staat in log en response;
- configuratie-import preview/commit logt alleen aantallen;
- endpoint met fixturesecret: secret komt nergens in logs;
- run van alle zes evaluatoren produceert lifecycle/progress/gate-events;
- retry en failed stream produceren veilige WARNING/ERROR-events;
- parallelle workers behouden correcte run/case/executioncontext.

### Releasegate

CI draait E2E met `LOG_LEVEL=DEBUG`, slaat stderr op en faalt wanneer:

- bekende fixturesecrets voorkomen;
- fixtureprompt of kandidaatantwoord voorkomt;
- vereist lifecycle-event ontbreekt;
- logregel groter dan limiet is;
- traceback voorkomt bij succesvolle E2E.

## 11. Gefaseerd implementatieplan

1. **Contract en helper**: `log_event`, allowlists, formatter, redaction en tests.
2. **Requestcorrelatie**: alle GUI/API-requests, timing en responseheader.
3. **Lifecycle**: startup, migraties, recovery, workers, server en shutdown.
4. **Gebruikersmutaties**: semantische INFO-events in services voor instellingen, endpoints, modellen, sets, judges, configuratie en runs.
5. **Queue/run**: transitions, progress-throttling, cancellation, rerun, retention en gates.
6. **Provider DEBUG**: limiter, retries, DNS-classificatie, streamingmetrics en judgeparser zonder content.
7. **CLI**: migrate, backup en restore events.
8. **E2E leakscan en volumetest**.
9. **Documentatie**: eventcatalogus, troubleshooting, native/Docker capture en retentie.
10. **Optioneel**: JSON-format en roterend lokaal bestand na operationele behoefte.

Elke fase vereist Ruff, strict Mypy, unit/integratietests en secret-leakscan voordat volgende fase start.

## 12. Acceptatiecriteria

Bij `LOG_LEVEL=INFO` kan beheerder uit logs reconstrueren:

- wanneer app startte en stopte;
- welke lokale mutaties via GUI/API zijn uitgevoerd;
- welke run gestart, geannuleerd of voltooid is;
- voortgang en aantallen zonder testcase-inhoud;
- welke provider-/jobcategorie faalde;
- gate-eindstatus.

Bij `LOG_LEVEL=DEBUG` kan ontwikkelaar daarnaast reconstrueren:

- request- en workerstappen met gedeelde correlatie-ID's;
- queueclaim, lease, providerpogingen, streamingmetrics, evaluator- en commitstappen;
- retry/backoff en veilige foutclassificatie.

Geen niveau onthult secrets of prompt-/antwoordinhoud.
