# Implementatieplan — LLM Evaluator

**Status:** uitvoerbaar plan v1.1  
**Bron:** `specification.md` v1.0  
**Werkwijze:** kleine, opeenvolgende stappen; iedere stap eindigt met geautomatiseerde unit- en integratietests en laat hoofdbranch werkend achter.

## 1. Uitvoeringsregels

### 1.1 Definition of Done per stap

Een stap is pas gereed wanneer:

1. productiecode en migraties voor die stap compleet zijn; iedere nieuwe migratie heeft een `N → N+1`-fixture met representatieve bestaande data en een rollback-/onderbrekingstest;
2. genoemde unittests zijn toegevoegd en slagen;
3. genoemde integratietests zijn toegevoegd en slagen;
4. bestaande tests blijven slagen;
5. gewijzigde API-contracten en configuratie zijn gedocumenteerd;
6. geen echte provider, API-key of internetverbinding nodig is voor tests;
7. geen secrets, prompts of antwoorden in testlogs verschijnen;
8. relevante lint-, format- en typechecks slagen.

### 1.2 Testlagen

- **Unit:** pure domeinlogica, Pydantic-validatie, serializers, parsers en services met dependencies gemockt.
- **Integratie:** Flask testclient, tijdelijke SQLite-database, echte repositories/migraties en lokale mock-HTTP-provider.
- **GUI-integratie:** server-rendered HTML via Flask testclient, plus browserrooktests voor kritieke flows.
- **End-to-end:** volledige applicatie met mock-provider en worker, zonder extern netwerk.

### 1.3 Testconventies

Voorgestelde structuur:

```text
src/evaluator/
  api/
  domain/
  evaluators/
  providers/
  repositories/
  services/
  templates/
  static/
  workers/
tests/
  unit/
  integration/
  e2e/
  fixtures/
```

Tests gebruiken:

- `pytest` en Flask testclient;
- tijdelijke SQLite-bestanden, niet alleen `:memory:`, voor queue- en restarttests;
- een lokale OpenAI-compatible mockserver voor streaming, retries en discovery;
- één `Clock`-port met `monotonic()` voor latency/backoff en `now_utc()` voor persistente leases/retentie;
- een dual fake clock waarvan monotone tijd en UTC-wandklok onafhankelijk bestuurbaar zijn;
- deterministic UUID-/tijd-factories waar snapshots worden vergeleken;
- geen `sleep` in unittests.

### 1.4 Ondersteunde MVP-runtime

- Eén OS-host en uitsluitend een lokaal filesystemvolume voor SQLite-database, WAL en exports; netwerkfilesystems en gedeelde volumes zijn niet ondersteund.
- Eén applicatieproces bevat Flask plus een begrensde pool workerthreads. Alle providercalls — target, judge, verbindingstest en discovery — lopen door één thread-safe centrale endpointlimiter in dit proces.
- Meerdere applicatie- of workerprocessen zijn buiten MVP. Startupvalidatie weigert een geconfigureerde multiproces-topologie. Productieserver gebruikt exact één proces; Flask development reloader staat uit zodra workers actief zijn.
- Eén startupcoördinator in het applicatieproces voert migraties en recovery uit vóór HTTP-verkeer en workerthreads starten.
- SQLite gebruikt WAL alleen op het lokale volume. Deploymentdocumentatie vermeldt ondersteunde locking-, backup- en volumevereisten.
- Workerconcurrency is threadconcurrency en wordt begrensd door operationele settings. Centrale limiter is gedeeld door web- en workerthreads en sluit vóór database-/processhutdown.

### 1.5 Doorlopende verificatie

Na iedere stap minimaal:

```bash
pytest tests/unit tests/integration
```

Zodra tooling bestaat ook:

```bash
ruff check .
ruff format --check .
mypy src
```

Vanaf GUI-stappen:

```bash
npm run lint
npm run typecheck
npm run build
```

Indien geen npm-bundler nodig blijkt, vervang dit door gekozen TypeScript/JavaScript-checks en leg commando vast in `README.md`.

## 2. Implementatiefasen en stappen

## Fase A — Fundament

### Stap 1 — Projectskelet en reproduceerbare ontwikkelomgeving

**Doel**

Startbare Flask-app met app factory, Python-package, testconfiguratie en basisdocumentatie.

**Implementatie**

- Maak `.venv`-workflow, `requirements.txt`, `.gitignore`, `.env` en `.env.example`.
- Voeg Flask app factory en `python-dotenv` met `override=True` toe.
- Voeg `/health` toe zonder databaseafhankelijkheid.
- Configureer Python logging; gebruik nergens `print`.
- Voeg pytest-, lint-, format- en typecheckconfiguratie toe.
- Voeg minimale Nederlandse basispagina toe.
- Valideer runtime-topologie: één proces, lokaal SQLite-volume en development reloader uit bij actieve workers.
- Maak `README.md` met installatie- en testcommando's voor PowerShell en POSIX-shell.

**Unittests**

- Appconfig kiest defaults wanneer waarden ontbreken.
- `.env` overschrijft reeds bestaande proces-environmentwaarden.
- Ongeldige booleans/getallen leveren startup-validatiefout.
- Loggingconfig bevat geen body-/promptvelden.
- Multiprocesconfiguratie en een expliciet netwerkfilesystemvolume worden geweigerd.

**Integratietests**

- App factory start in testmodus.
- `GET /health` geeft `200` en stabiel JSON-contract.
- Basisroute rendert Nederlandse pagina.
- Teststart maakt geen productie-database of exportbestand aan.

**Acceptatie**

Schone checkout kan app en tests starten met gedocumenteerde commando's.

---

### Stap 2 — Gelaagde configuratie en instellingen

**Doel**

Operationele instellingen ondersteunen prioriteit `.env > GUI/database > default`.

**Implementatie**

- Definieer getypeerd settingsregister met default, datatype, bereik, geheim-status en env-naam.
- Implementeer bronresolutie en bronmetadata.
- Neem alle limieten, concurrency, retentie, timeouts, host/poort en logging uit specificatie op.
- Maak repository-interface voor database-instellingen; gebruik tijdelijk een in-memory adapter totdat SQLite in stap 3 beschikbaar is.
- Zorg dat environmentoverrides read-only zijn voor latere GUI/API.

**Unittests**

- Elke instelling gebruikt juiste default.
- Databasewaarde overschrijft default.
- Environmentwaarde overschrijft databasewaarde.
- Bron is correct: `default`, `database` of `environment`.
- Positieve limieten en `DEFAULT_RUN_CONCURRENCY <= MAX_RUN_CONCURRENCY` worden gevalideerd.
- `RUN_RETENTION_DAYS=0` betekent onbeperkt.

**Integratietests**

- App start met minimale `.env`.
- App weigert startup bij ongeldige grenscombinaties.
- Environmentwaarde blijft leidend na poging tot databasewijziging.

**Acceptatie**

Alle operationele instellingen hebben één getypeerde definitie en voorspelbare bronprioriteit.

---

### Stap 3 — SQLite-infrastructuur en migraties

**Doel**

Transactionele persistente basis met reproduceerbare schema-initialisatie.

**Implementatie**

- Voeg SQLite connection factory toe met foreign keys, busy timeout en geschikte WAL-instellingen.
- Kies en configureer een transactioneel migratiemechanisme met oplopende schemahistorie.
- Maak tabellen voor settings en schemahistorie.
- Serialiseer migraties met een SQLite-lock; tweede startup wacht begrensd of faalt veilig.
- Maak vóór iedere schema-upgrade een consistente lokale SQLite-backup en documenteer restore.
- Implementeer transaction-/repositoryhelpers.
- Voeg CLI-commando's voor database-initialisatie/migratie en backup/restore toe.

**Unittests**

- Transactie commit bij succes en rollback bij exception.
- Repository mapt databasewaarden naar domeintypen.
- UTC timestamphelpers produceren timezone-aware waarden.

**Integratietests**

- Lege database migreert naar laatste versie.
- Migratie is idempotent.
- Foreign-key-overtreding faalt.
- Database-instelling blijft behouden na apprestart.
- Baseline `0 → 1` maakt verwacht schema; iedere latere `N → N+1`-fixture behoudt representatieve bestaande gegevens en constraints.
- Een geforceerde fout halverwege een migratie laat schema en data op versie `N` intact.
- Twee gelijktijdige appstarts worden door een SQLite-migratielock geserialiseerd; slechts één voert iedere migratie uit.
- Pre-migratiebackup kan naar een nieuw lokaal databasebestand worden hersteld en opnieuw gemigreerd.
- Twee test-appinstanties kunnen database gecontroleerd benaderen.

**Acceptatie**

Applicatie kan vanaf leeg bestand starten, migreren en settings persistent opslaan.

---

### Stap 4 — Uniform API-foutcontract, request-ID en OpenAPI-basis

**Doel**

Alle API-routes leveren veilige, consistente fouten en zijn gedocumenteerd.

**Implementatie**

- Voeg `/api/v1` blueprint toe.
- Implementeer fouttypen en mapping naar HTTP-statussen.
- Genereer request-ID per request en geef het terug in foutresponse/header.
- Voeg veilige exceptionhandler toe zonder stacktrace in response.
- Start OpenAPI-document met health- en settingsschema's.

**Unittests**

- Elk domeinfouttype mapt naar juiste code/status.
- Validatiefouten bevatten veld-details.
- Sanitizer verwijdert geregistreerde secrets uit fouttekst.

**Integratietests**

- Onbekende API-route geeft consistent JSON-foutcontract.
- Geforceerde interne fout lekt geen stacktrace of body.
- Iedere fout bevat request-ID.
- OpenAPI-document is syntactisch geldig en bevat geïmplementeerde routes.

**Acceptatie**

Nieuwe routes kunnen vanaf nu alleen via gedeeld fout- en OpenAPI-contract worden toegevoegd.

## Fase B — Security en kernconfiguratie

### Stap 5 — Secretopslag, redactie en veilige logging

**Doel**

API-keys en geheime headers versleuteld opslaan; gevoelige inhoud nooit loggen.

**Implementatie**

- Voeg encryptieservice op basis van `ENCRYPTION_KEY` toe.
- Maak een generieke `secret_records`-tabel, migratie en repository; endpoint-/headerreferenties volgen in stap 6.
- Ondersteun keyversie zodat latere rotatie mogelijk blijft.
- Voeg secretredactor toe voor logging en providermeldingen.
- Introduceer structured logcontext met run-, case- en execution/test-ID.
- Verbied request-/responsebodylogging in HTTP-client en Flask.

**Unittests**

- Encrypt/decrypt round-trip.
- Zelfde plaintext levert door nonce verschillende ciphertext.
- Verkeerde sleutel en beschadigde ciphertext falen veilig.
- Redactor verwijdert volledige en geprefixte secretwaarde.
- Logformatter neemt correlation-ID's op, geen prompts/antwoorden.

**Integratietests**

- Secretrepository round-trip bewaart ciphertext, keyversie en nonce; plaintext staat nooit in SQLite.
- Gesimuleerde providerexception met secret/body lekt niets naar captured logs.
- Repositoryupdate met lege secret behoudt bestaande waarde.

**Acceptatie**

Geautomatiseerde leaktest doorzoekt alle in deze stap beschikbare databasevelden, responses en logs op fixturesecret en vindt niets.

---

### Stap 6 — Endpointdomein, validatie en CRUD-API

**Doel**

Endpoints, authheader, prefix, extra headers en rate-limitconfiguratie persistent beheren.

**Implementatie**

- Maak endpoint- en headermodellen plus migratie; verwijs geheime waarden naar `secret_records` uit stap 5.
- Valideer unieke naam, headernamen, timeout, retries, RPM, TPM en concurrency.
- Implementeer HTTPS-/localhost-/trusted-internal-HTTP-regels.
- Implementeer CRUD-service en API-routes.
- Masker secrets bij lezen; hard delete blokkeren bij historische referenties voorbereiden.

**Unittests**

- Geldige/ongeldige HTTP-headernamen.
- HTTPS, localhost HTTP, loopback HTTP en expliciet vertrouwd intern HTTP.
- Niet-vertrouwd extern HTTP wordt geweigerd.
- Negatieve/ongeldige rate limits worden geweigerd; leeg betekent onbeperkt.
- API-keyprefix wordt correct gecombineerd zonder ongewenste trim.
- Secretvelden serialiseren uitsluitend gemaskeerd.

**Integratietests**

- Endpoint CRUD met gewone en geheime extra headers.
- Unieke naamconstraint.
- Patch zonder key behoudt key; patch met key vervangt hem.
- Database bevat alleen referenties en versleutelde secrets; API-responses bevatten nooit plaintext.
- Lege secretpatch behoudt bestaande waarde; nieuwe waarde vervangt via secretrepository.
- Redirectconfiguratie wordt niet toegestaan als impliciete host/protocolwijziging.
- OpenAPI bevat endpointcontracten.

**Acceptatie**

Endpoint kan volledig via API worden aangemaakt en teruggelezen zonder secretlek.

---

### Stap 7 — Modeltestprofielen en inferenceparameters

**Doel**

Meerdere profielen voor hetzelfde model-ID met temperatuur, reasoning en providerparameters beheren.

**Implementatie**

- Maak modelprofiel- en prijsmodellen plus migratie.
- Implementeer unieke weergavenaam per endpoint.
- Bouw requestparametercomposer met optionele temperatuur, top-p, max tokens en custom JSON.
- Ondersteun configureerbaar reasoningveld, inclusief genest pad, standaard/custom waarde en `off`-gedrag.
- Voeg CRUD-API toe.

**Unittests**

- Twee profielen mogen zelfde model-ID hebben.
- Dubbele weergavenaam binnen endpoint faalt.
- Genest reasoningpad bouwt correct object.
- `off` wordt verzonden of weggelaten volgens profiel.
- Niet-ondersteunde parameters worden niet als `null` verstuurd.
- Custom parameters kunnen beschermde kernvelden niet onbedoeld overschrijven.

**Integratietests**

- Modelprofiel CRUD onder endpoint.
- Cascade-/archivegedrag bij endpointdeactivatie.
- API round-trip behoudt Decimal-prijzen en inferenceparameters.
- Ongeldige gedeeltelijke hoge-contextprijsconfiguratie wordt geweigerd.

**Acceptatie**

Zelfde provider-model kan met meerdere unieke testprofielen worden geconfigureerd.

---

### Stap 8 — Kostenengine

**Doel**

Alle EUR-kosten deterministisch en zonder dubbeltelling berekenen.

**Implementatie**

- Maak usage- en pricing-value objects met `Decimal`.
- Implementeer standaard- versus hoog-contexttarief op `X`/`X+1`.
- Verwerk cached input en reasoning als subsets.
- Verwerk vaste requestkosten en onbekende usage.
- Bewaar berekeningsspecificatie/resultaat geschikt voor auditsnapshot.

**Unittests**

- Basis input/outputkosten.
- Exact `X` gebruikt standaardtarief; `X+1` hoog tarief.
- Cached/reasoningtokens worden niet dubbel geteld.
- Ontbrekend speciaal tarief valt correct terug.
- Onbekende subsetusage geeft afgesproken `unknown`-status.
- Subset groter dan totaal is ongeldig.
- Vaste kosten bij succesvolle/betaalde call en niet bij onbetaalde fout.
- Decimalprecisie en opslag op minimaal acht decimalen.

**Integratietests**

- Profiel uit database plus providerusage levert verwacht kostenrecord.
- Opgeslagen profielprijzen plus expliciet aangeleverde pricing-value objecten blijven tijdens één berekening immutable; historische runsnapshotdekking volgt in stap 19.
- API serialiseert bedragen als precieze decimale strings/getallen zonder floatartefacten.

**Acceptatie**

Kostenfixtures uit specificatie zijn exact reproduceerbaar.

## Fase C — Golden sets en evaluatoren

### Stap 9 — Golden sets en testcase-CRUD

**Doel**

Golden sets en testcases inclusief evaluatorconfiguratie persistent beheren.

**Implementatie**

- Maak golden-set- en itemtabellen met UUID, `case_id`, prompts, expected answer, methode, config, categorie, tags, gewicht, enabled en sortering.
- Definieer Pydantic-discriminated union voor evaluatormethoden.
- Dwing invoerlimieten en methodeafhankelijke verplichte velden af.
- Voeg CRUD- en paginatie-API toe.

**Unittests**

- Uniek `case_id` binnen set.
- Gewicht moet groter dan nul zijn.
- Prompt-/system-/expected-answerlimieten.
- `expected_answer` verplicht voor exact match.
- Eén optionele system prompt en precies één user prompt.
- Iedere evaluatorconfiguratie kiest juiste schema.

**Integratietests**

- Golden-set- en item-CRUD.
- Transactionele sorteerwijziging.
- Paginatie en filtering op categorie/tag/enabled.
- Verwijderen/archiveren respecteert referenties.
- Database JSON-config wordt bij lezen opnieuw gevalideerd.

**Acceptatie**

Volledige testcase uit specificatie kan via API worden opgeslagen en verliesvrij gelezen.

---

### Stap 10 — Exact-match- en contains-evaluatoren

**Doel**

Eerste deterministische evaluatoren inzetbaar maken.

**Implementatie**

- Definieer gedeeld evaluatorinterface en resultaatcontract.
- Implementeer normalisatie in vastgelegde volgorde.
- Implementeer `exact_match` en `contains` met `all`/`any`.
- Bewaar genormaliseerde waarden en matchmetadata.

**Unittests**

- CRLF/CR naar LF.
- Trim, casefold en collapse-whitespace afzonderlijk en gecombineerd.
- Volgorde van normalisatie.
- Exact match pass/fail.
- Contains `all`/`any`, lege fragmenten, overlap en Unicode.
- Resultaat bevat score `0/1`, passed en veilige reden.

**Integratietests**

- Evaluatorregistry kiest methode vanuit opgeslagen testcase.
- API-validatie weigert lege contains-fragmenten.
- Evaluatoroutput serialiseert en deserialiseert verliesvrij via het gedeelde resultaatcontract; databasepersistentie volgt in stap 19/20.

**Acceptatie**

Fixturecases leveren exact verwachte scores en redenen.

---

### Stap 11 — Regex- en numeric-evaluatoren

**Doel**

Patroon- en numerieke controles toevoegen.

**Implementatie**

- Implementeer regex search/full match en flags.
- Gebruik regexengine/uitvoering met afdwingbare timeout.
- Implementeer Decimal-parser voor punt/komma, optionele duizendtalscheiding en extractiemodus.
- Implementeer absolute en relatieve tolerantie.

**Unittests**

- Regex search/full match en iedere flag.
- Ongeldig patroon wordt vóór run afgewezen.
- Catastrophic pattern stopt binnen testbare timeout.
- Numeric entire-answer/first-number.
- Punt/komma en duizendtalscheiding.
- Absolute, relatieve en gecombineerde tolerantie op grenswaarden.
- Geen/ambigue waarde geeft fail, geen systeemfout.

**Integratietests**

- Opgeslagen regex-/numericconfig valideert bij runvoorbereiding.
- Regex-/numericoutput round-tript via het gedeelde resultaatcontract; databasepersistentie volgt in stap 19/20.
- Timeout van één regexcase beïnvloedt andere cases niet.

**Acceptatie**

Regex- en numerieke fixturematrix slaagt volledig.

---

### Stap 12 — JSON Schema-evaluator

**Doel**

JSON-antwoorden tegen Draft 2020-12 en optionele expected value beoordelen.

**Implementatie**

- Voeg JSON Schema-validator toe.
- Valideer schema bij opslaan/import/runvoorbereiding.
- Pak optionele enkele Markdown-codefence defensief uit.
- Implementeer optionele diepe JSON-gelijkheid.
- Geef compacte, begrensde validatiereden.

**Unittests**

- Geldig/ongeldig schema.
- Geldige/ongeldige kandidaat-JSON.
- Codefence aan/uit en omringende tekst.
- Nested object, arrays, required en types.
- Diepe gelijkheid onafhankelijk van object-keyvolgorde.
- Begrenzing van foutdetail.

**Integratietests**

- Ongeldig schema blokkeert testcase-opslag/import.
- Geldig schema evalueert vanuit databaseconfig.
- Modelcontent wordt bij HTML-weergave escaped voorbereid opgeslagen.

**Acceptatie**

Draft 2020-12 fixtures en codefencecases leveren verwachte uitkomst.

---

### Stap 13 — Golden-set JSON/CSV-import en -export

**Doel**

Golden sets atomair en verliesvrij uitwisselen.

**Implementatie**

- Implementeer canoniek JSON-formaat.
- Implementeer CSV-parser/serializer met JSON-velden voor configs/tags.
- Voeg bestands- en itemlimieten toe.
- Lever preview/validatiefouten met rij en case-ID.
- Maak import atomair.

**Unittests**

- JSON- en CSV-escaping, multiline prompts en Unicode.
- Booleans, Decimal-gewicht, tags en evaluatorconfig.
- Ongeldige rij rapporteert juiste locatie.
- Te groot bestand/set wordt geweigerd vóór schrijven.
- Exportvolgorde is stabiel.

**Integratietests**

- JSON round-trip behoudt alle velden.
- CSV round-trip behoudt alle ondersteunde velden.
- Eén ongeldige rij resulteert in nul imports.
- Dubbele `case_id` wordt duidelijk gemeld.
- Downloadheaders en bestandsnamen zijn correct.

**Acceptatie**

Voorbeeldset kan via beide formaten worden geëxporteerd, verwijderd en identiek teruggeplaatst.

## Fase D — Providerintegratie

### Stap 14 — OpenAI requestbuilder en niet-streaming client

**Doel**

Veilige gedeelde HTTP-basis en judge-compatible non-streaming completioncall.

**Implementatie**

- Bouw providerclient met timeouts, geen cross-host/protocol redirects en veilige headers.
- Composeer messages, model en inferenceparameters.
- Parse `choices[0].message.content`, usage en request-ID.
- Dwing responsegroottelimiet af.
- Classificeer HTTP-, netwerk-, timeout- en parsefouten.

**Unittests**

- Requestbody voor system+user en modelprofiel.
- API-keyprefix en custom headers, inclusief env-secretreferentie.
- Redirectbeleid.
- Responseparser voor geldig/ontbrekend/malformed contract.
- Foutclassificatie 401/403/429/4xx/5xx/timeout/network.
- Bodylimiet.

**Integratietests**

- Lokale mockprovider ontvangt exacte headers/body.
- Secretheaders verschijnen niet in logs.
- Redirect naar andere host/protocol wordt niet gevolgd.
- Providerfouten worden naar API-foutcategorie vertaald.

**Acceptatie**

Non-streaming mockcompletion werkt volledig zonder extern netwerk.

---

### Stap 15 — SSE-streaming, TTFT en TPS

**Doel**

Targetantwoorden streamen en prestatiemetrics betrouwbaar meten.

**Implementatie**

- Voeg `stream=true` en `stream_options.include_usage=true` toe.
- Bouw incrementele SSE-parser met gefragmenteerde regels/events.
- Assembleer `delta.content`, finale usage en `[DONE]`.
- Meet total latency, TTFT en generation duration met monotone klok.
- Bereken TPS alleen bij bekende outputtokens en positieve duur.
- Bewaar partiële tekst bij verbroken/te grote stream; markeer fout en evalueer niet.

**Unittests**

- Eén en meerdere chunks; bytes gesplitst midden in UTF-8/event.
- Lege role-delta telt niet als first token.
- Finale usage voor/zonder choices.
- `[DONE]`, provider-eindindicatie en onverwacht EOF.
- TTFT/latency met fake clock.
- TPS normaal, onbekende usage en nulduur.
- Stream-/antwoordgroottelimiet.

**Integratietests**

- Mock-SSE-provider levert samengesteld antwoord en metrics.
- Verbroken stream bewaart partieel antwoord, maar start geen evaluator.
- Finale cached/reasoning usage bereikt kostenengine.
- Verbindingstest rapporteert veilige status en TTFT.

**Acceptatie**

Streamingfixture produceert exact antwoord, tokenusage, latency, TTFT en TPS.

---

### Stap 16 — Retrybeleid en centrale endpoint-rate limiter

**Doel**

Providerverkeer binnen het ondersteunde single-processmodel centraal beheersen op RPM, TPM en concurrency.

**Implementatie**

- Implementeer retrybeslisser met exponential backoff en jitter via `Clock.monotonic()`.
- Respecteer `Retry-After` in seconden en HTTP-datumvorm; zet HTTP-datums via `Clock.now_utc()` om naar monotone wachtduren zonder persistente monotone timestamps.
- Maak bij appstartup exact één thread-safe `CentralEndpointLimiter` en injecteer dezelfde instantie in target-, judge-, verbindingstest- en discoveryclients.
- Houd per endpoint gedeelde RPM-, TPM- en concurrencytoestand bij over alle web- en workerthreads.
- Reserveer RPM/concurrency exact en TPM conservatief; reconcile usage en geef concurrency in `finally` vrij bij succes, fout of annulering.
- Expose een in-memory observerevent/wachtreden `rate_limited_wait`; persistente jobstatus volgt in stap 19/20.
- Sluit limiter gecontroleerd zodat wachtende calls annuleren vóór workers en database stoppen.

**Unittests**

- Retry alleen timeout/network/429/5xx; niet overige 4xx.
- Backoffreeks en jitter met seeded random en monotone fake clock.
- `Retry-After` parsing met onafhankelijk bestuurde UTC- en monotone fake clock.
- RPM-window en concurrency acquire/release vanuit meerdere threads.
- TPM-reservering, reconciliatie met echte usage en onbekende usage.
- Fout en annulering tijdens wachten/call lekken geen concurrencypermit of TPM-reservering.
- Shutdown maakt alle wachtende callers wakker met een gecontroleerde annulering.

**Integratietests**

- Mockprovider 429→succes levert twee calls en juiste observerevents; persistente attempts volgen in stap 20.
- Parallelle calls overschrijden endpointconcurrency niet.
- Target-, non-streaming judge- en verbindingstestcalls delen aantoonbaar dezelfde limiterinstantie; discovery wordt in stap 17 toegevoegd.
- Gelijktijdige target- en judgecalls delen RPM/TPM-budget en kunnen elkaar niet passeren.
- Appstartup weigert meer dan één proces; limitergedrag hoeft daarom niet procesoverschrijdend te zijn.

**Acceptatie**

Deterministische dual-clocktest bewijst dat geen providercall vóór beschikbare centrale capaciteit wordt verzonden en dat alle calltypen binnen één proces dezelfde endpointbudgetten delen.

---

### Stap 17 — Model discovery

**Doel**

Modellen handmatig of selectief via OpenAI-compatible `/models` beheren.

**Implementatie**

- Implementeer veilige URL-suggestie.
- Voeg authenticated `GET` naar models-URL toe via dezelfde `CentralEndpointLimiter` als alle overige providercalls.
- Parse `data[].id` en valideer responsegrootte.
- Voeg preview en selectieve import toe; overschrijf nooit bestaand profiel.

**Unittests**

- URL-afleiding voor `/chat/completions`, `/completions` en niet-afleidbare URL.
- Deduplicatie/validatie van `data[].id`.
- Naamconflict genereert voorstel zonder bestaand profiel te wijzigen.
- Discoveryfout bevat geen responsebody/secret.

**Integratietests**

- Mock `/models` ontvangt dezelfde auth/custom headers en deelt limiterbudget met een gelijktijdige targetcall.
- Selectieve import maakt alleen gekozen profielen.
- Bestaand model-ID kan met unieke profielnaam opnieuw worden geïmporteerd.
- Foutresponse verandert database niet.

**Acceptatie**

Discoveryflow importeert geselecteerde mockmodellen en behoudt bestaande profielen exact.

## Fase E — Judge, runs en queue

### Stap 18 — Judgeprofielen, rubric en outputparser

**Doel**

Judgeconfiguratie en deterministische verwerking van criterion-subresultaten.

**Implementatie**

- Maak judgeprofiel/template/rubricmodellen en migratie.
- Implementeer hiërarchie globaal → golden set → runoverride.
- Bouw prompt met veilig gescheiden prompt, expected answer, rubric, context en kandidaat.
- Parse JSON, optioneel uit één codefence.
- Valideer exacte criteriumset, subscores en redenen.
- Bereken gewogen totaalscore en threshold; bewaar judgewaarde als auditcontrole.

**Unittests**

- Standaard vijf criteria en gelijke gewichten.
- Rubric aanvullen/vervangen en positieve gewichten.
- Ontbrekende/dubbele/onbekende criteria worden geweigerd.
- Gewogen score en inclusieve threshold `0.7`.
- Judge-scoreafwijking `>0.01` wordt gemarkeerd.
- Prompt-injectietekst blijft geciteerde data.
- Codefence en malformed JSON.

**Integratietests**

- CRUD judgeprofiel en override-resolutie.
- Mockjudgeoutput parseert naar het gedeelde resultaatcontract met berekende score en redenen; resultaatstorage volgt in stap 19/20.
- Ongeldige output veroorzaakt via de service één retry en daarna `judge_parse_error`.
- Judgekosten/latency blijven in het contract apart van targetmetrics.

**Acceptatie**

Vaste judgefixture levert reproduceerbare subresultaten, totaalscore en pass/fail.

---

### Stap 19 — Runsnapshots en duurzame jobqueue

**Doel**

Een run atomair aanmaken met immutable snapshots en één job per case/model-combinatie.

**Implementatie**

- Maak run-, case-snapshot-, model-snapshot-, job-, result- en attempttabellen met migratie-evolutiefixture.
- Maak snapshotservice zonder secrets; snapshot prijzen en alle evaluator-/judgecontracten uit stappen 8 en 10–12/18.
- Valideer actieve cases/profielen, limieten en judgevereisten vóór start.
- Maak jobs transactioneel aan.
- Modelleer leasevelden `lease_owner`, strikt oplopende `lease_generation` en timezone-aware UTC `lease_expires_at`; nooit persistente monotone tijd.
- Implementeer atomische claim die owner zet, generation verhoogt en expiry via `Clock.now_utc()` bepaalt.
- Implementeer heartbeat als conditionele compare-and-setupdate op job-ID + owner + generation; heartbeat verlengt alleen een nog geldige eigen lease.
- Vereis hetzelfde fencingtoken voor iedere attempt-, status-, resultaat- en jobafrondingswrite. Stale writes wijzigen nul rijen en leveren een expliciete `LeaseLost`-uitkomst.
- Handhaaf unieke resultaatconstraint als tweede verdedigingslaag, niet als vervanging voor fencing.

**Unittests**

- Jobaantal = enabled cases × geselecteerde profielen.
- Snapshot bevat alle vereiste config/prijzen maar geen secrets.
- Statusovergangen zijn geldig; ongeldige overgang faalt.
- Leaseclaim/-expiry via onafhankelijk bestuurde UTC fake clock; monotone klokwijziging beïnvloedt lease niet.
- Iedere nieuwe claim verhoogt `lease_generation`, ook bij dezelfde owner.
- Heartbeat en alle conditionele writes slagen alleen voor actuele owner + generation + niet-verlopen lease.
- Stale worker krijgt `LeaseLost` en kan attempt, status, resultaat noch afronding schrijven.
- Runlimieten en lege selectie.

**Integratietests**

- Runaanmaak schrijft snapshots/jobs in één transactie.
- Fout tijdens snapshot laat geen halve run achter.
- Configwijziging na start verandert snapshot niet.
- Twee workerthreads kunnen dezelfde job niet tegelijk claimen.
- Lange call met periodieke heartbeat wordt niet door tweede workerthread overgenomen.
- Na UTC-expiry claimt tweede worker met hogere generation; late heartbeat/commit van eerste worker wordt volledig geweigerd.
- Unieke resultconstraint voorkomt dubbele lokale resultaten als aanvullende invariant.
- Migratie van vorige schemaversie behoudt Decimal-prijzen, timezone-aware timestamps, evaluator-/judge-JSON en bestaande ciphertextreferenties.

**Acceptatie**

Run is volledig reproduceerbaar uit snapshots; queueclaims en alle daaropvolgende writes zijn door owner plus generation gefenced.

---

### Stap 20 — Workerpipeline en runstatus

**Doel**

Jobs uitvoeren: targetcall → evaluator/judge → kosten → resultaat → aggregatiestatus.

**Implementatie**

- Bouw workerloop met dependency-injectie en begrensde threadpool binnen het enige applicatieproces.
- Verbind streaming client, centrale limiter, retries, evaluatorregistry en judge.
- Start na claim een heartbeatloop met interval ruim korter dan leaseduur; stop provider-/pipelinewerk zo snel mogelijk na `LeaseLost`.
- Sla iedere request attempt en veilige foutmetadata alleen op met actuele owner + generation.
- Commit evaluator-/judgeresultaat, kosten en jobafronding in één conditionele transactie met hetzelfde fencingtoken.
- Bereken runstatus en voortgang.
- Implementeer annulering en rerun-failures als nieuwe run.

**Unittests**

- Pipelinevolgorde en evaluatorselectie.
- Partiële stream wordt niet geëvalueerd.
- Eén jobfout beïnvloedt volgende job niet.
- Runstatus completed/completed_with_errors/failed/cancelled.
- Annulering slaat pending jobs over; actieve call mag afronden.
- Attempt-/retryregistratie.
- Heartbeats stoppen bij afronding, annulering, `LeaseLost` en shutdown.
- Iedere pipelinewrite draagt exact hetzelfde owner/generation-token.

**Integratietests**

- Run met meerdere cases, modellen en deterministische evaluatoren voltooit.
- Run met LLM judge voltooit en splitst target-/judgekosten.
- Gemengde providerfouten leveren `completed_with_errors`.
- Cancelroute stopt niet-gestarte jobs.
- Rerun-failures bevat alleen mislukte combinaties als nieuwe snapshots/jobs.
- Geforceerde leaseovername tijdens geblokkeerde providercall: oude worker kan daarna geen attempt/resultaat/status committen; nieuwe worker voltooit één lokaal resultaat.

**Acceptatie**

Volledige headless run tegen mockprovider eindigt met correcte resultaten en status.

---

### Stap 21 — Restart-recovery en leaseherstel

**Doel**

Actieve runs na procesrestart veilig hervatten.

**Implementatie**

- Voer startup recovery uit vóór workers starten.
- Maak uitsluitend via UTC verlopen leases opnieuw claimbaar; verhoog generation pas bij nieuwe claim en behoud oude owner/generation voor diagnose.
- Markeer hervatte jobs `recovered_after_interruption` via het nieuwe geldige fencingtoken.
- Behoud afgeronde/definitief gefaalde jobs.
- Voeg waarschuwing voor mogelijke dubbele providerkosten toe.

**Unittests**

- Alleen via `Clock.now_utc()` verlopen lease wordt opnieuw beschikbaar; monotone reset/restart verandert dit niet.
- Niet-verlopen lease met ontbrekende heartbeat blijft tot expiry afgeschermd.
- Afgeronde job blijft onaangeraakt.
- Runstatus wordt correct hersteld.
- Recoverylogging bevat IDs en aantallen, geen content.

**Integratietests**

- Stop worker na claim, verschuif UTC fake clock voorbij expiry, start nieuwe app en voltooi job met hogere generation.
- Reeds voltooid resultaat wordt niet opnieuw aangeroepen.
- Oude worker hervat na recovery maar al zijn late writes worden door fencing geweigerd.
- Resultaat plus jobcommitcrashscenario veroorzaakt geen dubbel lokaal resultaat.
- Recoverymarker en waarschuwing zijn via API zichtbaar.

**Acceptatie**

Gesimuleerde restart hervat uitsluitend onafgeronde combinaties.

## Fase F — Metrics, gates en uitwisseling

### Stap 22 — Aggregaties en rangschikking

**Doel**

Per model/run alle kwaliteits-, timing-, token- en kostenmetrics berekenen.

**Implementatie**

- Implementeer gewogen score, pass/error rate en counts.
- Implementeer gemiddelde, mediaan en p95 voor latency, TTFT en TPS.
- Sommeer tokenklassen en target-/judgekosten.
- Bereken kosten per geëvalueerde/passed case.
- Rangschik score → error rate → kosten.
- Toon unknown expliciet en sluit die correct uit aggregaties.

**Unittests**

- Gewichten, errors en lege populaties.
- Mediaan/p95 voor kleine en grotere sets met vastgelegde percentilemethode.
- Unknown TTFT/TPS/kosten.
- Kosten per passed case bij nul passes.
- Deterministische tie-breakrangschikking.

**Integratietests**

- Resultaatfixtures in SQLite leveren verwachte modelaggregaten.
- API-filters veranderen resultaatslijst, niet opgeslagen totaalcijfers.
- Snapshotprijzen sturen historische kostenaggregatie.

**Acceptatie**

Golden aggregate fixture produceert exact verwachte tabel en rangorde.

---

### Stap 23 — Quality gates

**Doel**

Golden-setdefaults en runoverrides evalueren naar gate-uitkomst.

**Implementatie**

- Definieer gateschema en persistente defaults/overrides.
- Evalueer per-modelgates en totale runkostengate.
- Implementeer `not_evaluated`, `passed`, `failed`, `indeterminate`.
- Bewaar per gate gemeten waarde, grens en uitkomst.

**Unittests**

- Inclusieve min/maxgrenzen.
- Iedere ondersteunde gate.
- Eén falend model faalt run-gate.
- Unknown vereiste metric geeft `indeterminate`.
- Geen gates en niet-terminale/geannuleerde run geven `not_evaluated`.
- Runoverride vervangt defaults volledig volgens contract.

**Integratietests**

- Terminale run evalueert gates automatisch.
- API toont gate-uitkomsten per model.
- Totale kostengate bevat target plus judge.
- Resultaat blijft stabiel na wijziging golden-setdefaults door snapshot.

**Acceptatie**

Gatefixture dekt alle vier uitkomsten.

---

### Stap 24 — Volledige configuratie-JSON import/export

**Doel**

Functionele configuratie verliesvrij uitwisselen zonder secrets.

**Implementatie**

- Definieer versiegebonden Pydantic-schema `1.0`.
- Exporteer endpoints/headers, profielen/prijzen, judgeprofielen, golden sets en gates.
- Exporteer secretwaarde altijd `null`, eventueel met env-varreferentie.
- Implementeer preview, conflictmap, `merge` en `replace`.
- Archiveer historisch gebruikte records bij replace.
- Voer import atomair uit.

**Unittests**

- Serializer bevat alle functionele velden.
- Geen ciphertext/plaintextsecret in JSON.
- Schema-/referentie-/UUID-conflicten.
- Stabiele UUID behouden versus remappen.
- Merge- en replace-planner zonder database-effecten.

**Integratietests**

- Volledige export→lege database→import round-trip.
- Ontbrekende secrets worden gemarkeerd en blokkeren providercall tot ingevuld.
- Merge overschrijft niets buiten previewplan.
- Replace archiveert gebruikte records en behoudt historie.
- Eén validatiefout rolt volledige import terug.

**Acceptatie**

Semantische vergelijking vóór export en na import is gelijk, uitgezonderd bewust verwijderde secrets.

---

### Stap 25 — Resultaatexports en retentie

**Doel**

Runs exporteren als JSON/CSV/offline HTML en oude runs veilig opruimen.

**Implementatie**

- Bouw volledige JSON-export en vlakke CSV-export.
- Bouw zelfstandig HTML-rapport met inline CSS, print-CSS en strikte CSP.
- Escape alle providercontent.
- Voeg gevoelige-inhoudwaarschuwing toe.
- Implementeer handmatige runverwijdering en periodieke retentiecleanup.

**Unittests**

- CSV quoting, Unicode en stabiele kolommen.
- HTML escaping van script-/markupcontent.
- CSP en geen externe URL/assets/scripts.
- Bestandsnaamsanitizing.
- Retentieselectie gebruikt `Clock.now_utc()` en UTC-eindtijd; actieve runs uitgesloten; `0` onbeperkt; monotone tijd wordt nooit persistent gebruikt.

**Integratietests**

- JSON/CSV/HTML bevatten metrics, snapshots en subresultaten, geen secrets.
- Export negeert actieve UI-filters en bevat volledige run.
- HTML opent zonder netwerkafhankelijkheden en bevat print-CSS.
- Handmatige delete en cleanup verwijderen afhankelijke records atomair.
- Cleanuprollback bij geforceerde fout behoudt hele run.

**Acceptatie**

Drie exports uit één fixture-run zijn bruikbaar, volledig en secretvrij.

## Fase G — GUI

### Stap 26 — GUI voor instellingen, endpoints en modellen

**Doel**

Alle operationele instellingen, endpoints, headers en modelprofielen via Nederlandse GUI beheren.

**Implementatie**

- Maak gedeelde layout, navigatie, centrale Nederlandse UI-teksten en CSRF.
- Bouw settingsscherm met bronbadge en read-only environmentoverrides.
- Bouw endpointlijst/formulier met passwordveld, headers, trusted-HTTP-waarschuwing, rate limits, test en discovery.
- Bouw modelprofielformulier met inferenceparameters, reasoning en volledige prijsconfiguratie.
- Voeg toegankelijke validatie- en bevestigingsmeldingen toe.

**Unittests**

- Viewmodel maskeert secrets en toont correcte bronbadge.
- Formmapping behoudt lege secret als “niet wijzigen”.
- Nederlandse tekstcatalogus bevat gebruikte sleutels.
- Price-/reasoningform naar domeinmodel en terug.

**Integratietests**

- Flask HTML-routes renderen zonder secrets.
- CSRF blokkeert ongeldige mutaties.
- Endpoint plus custom headers via GUI aanmaken/wijzigen.
- Environmentoverride is zichtbaar maar niet bewerkbaar.
- Discoveryselectie maakt profiel; verbindingstest toont veilige uitkomst.

**GUI-rooktests**

- Toetsenbordflow door endpoint- en modelformulier.
- Labels, focus en foutkoppeling aanwezig.
- Desktoplayout werkt op 1024 px.

**Acceptatie**

Gebruiker configureert zonder API-client een endpoint met headers en twee modelprofielen.

---

### Stap 27 — GUI voor golden sets, judge en configuratie-uitwisseling

**Doel**

Testcases, evaluatoren, judge en volledige JSON-configuratie via GUI beheren.

**Implementatie**

- Bouw golden-setlijst, itemeditor en dynamische evaluatorvelden.
- Bouw JSON/CSV-importpreview en exportacties.
- Bouw judgeprofiel-/rubriceditor met gewichten en template.
- Bouw configuratie-JSON import/export met merge/replace-preview en missing-secretsstatus.
- Toon actieve limieten in formulieren.

**Unittests**

- Form/viewmodel per evaluatortype.
- Rubricgewicht- en criteriumvalidatie.
- Importpreviewpresentatie van rij-/veld-errors en conflicten.
- Secretstatus toont nooit secretwaarde.

**Integratietests**

- Iedere evaluatormethode kan via GUI worden opgeslagen.
- Golden-set JSON/CSV-import werkt via multipart upload.
- Judgehiërarchie wordt correct getoond.
- Configuratie-export/download en importpreview/commit werken.
- Replace vereist expliciete bevestiging.

**GUI-rooktests**

- Maak golden set met minstens één testcase per evaluator.
- Importeer configuratie met ontbrekend secret en vul secret daarna via GUI in.
- Alle formulieren zijn toetsenbordbedienbaar.

**Acceptatie**

Volledige functionele configuratie kan zonder handmatige database/API-bewerking worden beheerd.

---

### Stap 28 — GUI voor runs, voortgang en resultaten

**Doel**

Runs starten, volgen, annuleren, vergelijken en exporteren.

**Implementatie**

- Bouw dashboard, nieuwe-runwizard en runhistorie.
- Toon omvang, profielen, judgeoverride, concurrency en quality gates vóór start.
- Poll runstatus maximaal iedere twee seconden.
- Bouw resultatenvergelijking, sortering, filters en itemdetail.
- Toon latency, TTFT, TPS, tokens, kosten, judge-subresultaten, errors en gate-uitkomsten.
- Voeg cancel, rerun-failures, delete en exports met bevestiging/waarschuwing toe.

**Unittests**

- Viewmodels voor unknown metrics, geldbedragen en lokale tijd.
- Sorteer-/filterparameterparsing.
- Voortgangsberekening.
- Status- en gatebadges.

**Integratietests**

- Run starten via GUI maakt juiste snapshots/jobs.
- Pollendpoint toont oplopende voortgang.
- Resultaatfilters en itemdetail tonen juiste data zonder secrets.
- Cancel/rerun/delete vereisen CSRF en bevestigingsflow.
- Exportwaarschuwing en downloads werken.

**GUI-rooktests**

- Start mockrun met twee modellen en meerdere evaluatoren.
- Volg tot voltooiing, sorteer op score/TTFT/TPS/kosten en open detail.
- Annuleer tweede run en rerun failures.

**Acceptatie**

Primaire gebruikersflow uit specificatie is volledig via GUI uitvoerbaar.

## Fase H — Oplevering en hardening

### Stap 29 — Performance, security en robuustheid ✅

**Doel**

Niet-functionele grenzen aantoonbaar halen.

**Implementatie**

- Voeg paginatie/indexen en gerichte aggregatequeries toe.
- Harden CSP, CSRF, securityheaders, upload- en responslimieten.
- Controleer SSRF, redirect, secretredactie en HTML escaping centraal.
- Optimaliseer SQLite-writepatronen en busy handling.
- Voeg gecontroleerde shutdown van workers en leases toe.

**Unittests**

- Securityheaderconfig.
- URL/redirect edge cases: IPv4/IPv6 loopback, userinfo, hostnamevarianten.
- Upload-/streamlimieten op exacte grens en grens+1.
- Shutdownstatusovergangen.

**Integratietests**

- 10.000 resultaatfixtures: gepagineerde lijst en aggregaten binnen afgesproken testbudget op referentieprofiel.
- 20 modelprofielen/run en configureerbare concurrency.
- SQLite busy/retryscenario zonder dataverlies.
- XSS-fixtures blijven tekst in GUI en HTML-export.
- Geautomatiseerde repository-/log-/response-scan vindt fixturesecrets niet.

**Acceptatie**

Security- en performancetestverslag documenteert gemeten resultaten en eventuele hardwarecontext.

**Status:** voltooid op 2026-11-06. Bewijs en restrisico's staan in `hardening-report.md`; releasegate: 348 tests, Ruff lint/format en strict Mypy geslaagd.

---

### Stap 30 — Docker, documentatie en volledige end-to-endacceptatie ✅

**Voltooid op 2026-11-06.** Production Waitress-entrypoint, non-root multi-stage image,
Compose-local volume/healthcheck, volledig gepinde runtimeconstraints, CI-releasegates,
offline mockprovider, versiegebonden releasefixture en native offline E2E zijn toegevoegd.
Docker/Compose waren niet geïnstalleerd op de uitvoerhost; echte image build/start/health wordt
daarom verplicht door `.github/workflows/ci.yml` en kan lokaal pas op een Dockerhost worden
bevestigd. Statische containercontracttests en volledige native productionpipeline-E2E slagen.

**Doel**

Reproduceerbare oplevering vanaf lege checkout.

**Implementatie**

- Maak productiegeschikte `Dockerfile` en `docker-compose.yml` met exact één applicatieproces en begrensde web-/workerthreads; geen Flask reloader.
- Voeg één Docker-local persistent volume voor SQLite/WAL/exports, lokale poortbinding en healthcheck toe; documenteer dat bind mounts alleen ondersteund zijn wanneer ze aantoonbaar een lokaal filesystem gebruiken.
- Laat containerentrypoint als enige startupcoördinator eerst backup/migraties en recovery uitvoeren en pas daarna HTTP-server en workerthreads starten.
- Rond `.env.example`, lokale `.env`, README en OpenAPI af.
- Voeg voorbeeld-golden-set en mockprovider toe.
- Documenteer Windows/POSIX, Docker, configuratiebronnen, JSON-schema, security, backup en beperkingen.

**Unittests**

- Configschema en JSON-voorbeelden valideren tegen applicatiemodellen.
- Documentatiecodeblokken/configvoorbeelden worden waar mogelijk machinegetest.

**Integratietests**

- Dockerimage bouwt.
- Container start met exact één applicatieproces, healthcheck wordt gezond en lokaal databasevolume overleeft restart.
- Composeconfig bevat geen netwerkfilesystem/gedeeld volume en publiceert geen tweede app- of workerreplica.
- OpenAPI bevat alle werkelijke `/api/v1`-routes en valideert.
- Migratie vanaf lege database en upgrade van vorige releasefixture met bestaande data werken in container; backup kan worden hersteld en opnieuw gemigreerd.

**End-to-endacceptatietest**

1. Start applicatie en mockprovider via Docker Compose.
2. Importeer functionele configuratie-JSON zonder secrets.
3. Vul secret via GUI in.
4. Discover twee mockmodellen en maak meerdere temperatuur-/reasoningprofielen.
5. Importeer golden set met alle zes evaluatiemethoden.
6. Start run met judge en quality gates.
7. Controleer live voortgang, streamingmetrics, kosten en rangschikking.
8. Herstart applicatie tijdens tweede run en controleer queueherstel.
9. Exporteer JSON, CSV en HTML; scan op secrets.
10. Controleer retentie-/deleteflow en auditmetadata.

**Acceptatie**

Volledige testsuite en end-to-endacceptatie slagen vanaf schone checkout zonder extern netwerk.

## 3. Traceabilitymatrix

| Specificatiegebied | Implementatiestappen |
|---|---|
| Configuratie en `.env`-prioriteit | 1–3, 24, 26 |
| Security, secrets, logging, SSRF | 4–6, 14, 24–26, 29 |
| Endpoints, headers en discovery | 6, 14, 16, 17, 26 |
| Modelprofielen, reasoning en prijzen | 7, 8, 26 |
| Golden sets en testcases | 9, 13, 27 |
| Deterministische evaluatoren | 10–12 |
| LLM judge en subresultaten | 14, 18, 20 |
| Streaming, TTFT en TPS | 15, 20, 22 |
| Rate limiting en retries | 16, 17, 20 |
| Runtime-topologie, lokale opslag en centrale limiter | 1, 3, 16, 20, 30 |
| Runs, snapshots, fencing en duurzame queue | 19–21 |
| Migratie-evolutie en restore | 3, alle schemastappen, 30 |
| Metrics en rangschikking | 8, 15, 22 |
| Quality gates | 23, 28 |
| Configuratie-JSON | 24, 27 |
| Resultaatexports en retentie | 25, 28 |
| Volledige GUI | 26–28 |
| Performance en hardening | 29 |
| Docker, docs en E2E | 30 |

## 4. Mijlpalen

### M1 — Configureerbare kern

Na stap 8:

- app, database en settings werken;
- endpoints/headers en modelprofielen bestaan;
- secrets zijn veilig;
- kostenengine is getest.

### M2 — Headless evaluatiecomponenten

Na stap 18:

- alle evaluatoren werken;
- golden sets importeren/exporteren;
- providerstreaming, discovery, limiter en judge werken afzonderlijk.

### M3 — Volledige headless evaluator

Na stap 25:

- runs, queue, recovery, metrics, gates, configuratie-uitwisseling en exports werken via API;
- kern kan volledig geautomatiseerd worden getest vóór GUI-bouw.

### M4 — Functionele GUI

Na stap 28:

- alle primaire functies zijn via Nederlandse GUI beschikbaar;
- endpoints en headers zijn volledig via GUI beheerbaar.

### M5 — MVP-release

Na stap 30:

- hardening, Docker, documentatie en end-to-endacceptatie afgerond;
- applicatie voldoet aan `specification.md`.

## 5. Releasegate

MVP mag alleen worden opgeleverd wanneer:

```bash
pytest
ruff check .
ruff format --check .
mypy src
# plus gekozen frontend lint/typecheck/build
# plus Docker build en end-to-endacceptatietest
```

alle slagen en daarnaast:

- geen test is overgeslagen zonder vastgelegde reden;
- geen echte API-key in repository, logs, fixtures of artifacts staat;
- OpenAPI en database migraties actueel zijn;
- README exact overeenkomt met werkelijke start- en testcommando's;
- configuratie- en resultaatexports door automatische secret-scan komen;
- alle traceabilityrijen aantoonbaar gereed zijn.
