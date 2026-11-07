# Specificatie — LLM Evaluator

**Status:** definitief v1.0  
**Taal gebruikersinterface:** Nederlands; code, API-contracten, logs en technische documentatie zijn Engels  
**Doel van dit document:** complete, implementeerbare productspecificatie voor bouw, test en oplevering van een lokale webapplicatie waarmee meerdere LLM-modellen reproduceerbaar worden geëvalueerd op een golden set.

> Deze versie bevat definitieve MVP-product- en implementatiebesluiten en vormt basis voor ontwikkeling, tests, build en acceptatie.

## 1. Doel en succescriteria

De applicatie laat een gebruiker:

1. één of meer configureerbare LLM-endpoints beheren;
2. per endpoint één of meer modellen configureren;
3. golden sets met prompts, verwachte antwoorden en evaluatiemethode beheren;
4. geselecteerde modellen tegen dezelfde golden set uitvoeren;
5. antwoorden automatisch beoordelen met minimaal `exact_match` en `llm_judge`;
6. kwaliteit, latency, tokengebruik en kosten per model en run vergelijken;
7. resultaten bewaren, inspecteren en exporteren.

De MVP is geslaagd wanneer een gebruiker zonder codewijzigingen twee modellen op verschillende endpoints kan configureren, één golden set kan uitvoeren, voortgang kan volgen en een reproduceerbaar vergelijkingsrapport kan bekijken en exporteren.

## 2. Scope

### 2.1 MVP

- Lokale single-user webapplicatie.
- CRUD via GUI voor endpoints, headers, modellen, judgeprofielen, golden sets, evaluatorconfiguratie en quality gates.
- JSON-import/export voor alle functionele configuratie, inclusief endpoints en headers, maar zonder secretwaarden.
- Handmatige invoer en import/export van golden-set-items.
- Evaluatiemethoden:
  - exacte overeenkomst (`exact_match`);
  - fragment aanwezig (`contains`);
  - reguliere expressie (`regex`);
  - numerieke vergelijking met tolerantie (`numeric`);
  - JSON Schema-validatie (`json_schema`);
  - LLM as a judge (`llm_judge`).
- Batch-run over gekozen golden set en modellen.
- Persistente opslag van configuratie, runs, ruwe antwoorden, scores en fouten.
- Dashboard met geaggregeerde en itemniveau-resultaten.
- Kostenberekening op basis van configureerbare prijzen en werkelijk gerapporteerde tokens.
- JSON-, CSV- en zelfstandig HTML-rapport van resultaten.
- Veilig beheer van API-keys.
- Automatische tests en lokale installatie-instructies.

### 2.2 Buiten MVP

- Multi-user accounts, rollen en SSO.
- Publieke cloudhosting of gedistribueerde workers.
- Fine-tuning of modeltraining.
- Human-reviewworkflow met meerdere beoordelaars.
- Semantische similarity als aparte evaluator.
- Statistische significantietoetsen.
- CI-integratie of command-line quality gates.
- Streaming judge-antwoorden; targetmodelantwoorden gebruiken wel streaming voor prestatiemetrics.
- Tool/function calling, afbeeldingen en andere multimodale invoer.

Deze punten kunnen later worden toegevoegd zonder datamodel of API fundamenteel te breken.

## 3. Gebruikersrol en hoofdflows

### 3.1 Rol

**Evaluatorbeheerder:** configureert alles, start runs en bekijkt/exporteert resultaten. MVP is een lokale single-userapplicatie zonder authenticatie.

### 3.2 Hoofdflow: configureren

1. Gebruiker maakt endpoint aan met naam, completions-URL, API-key en API-key-headernaam.
2. Applicatie test optioneel de verbinding.
3. Gebruiker voegt modellen toe met model-ID, weergavenaam, parameters en tokenprijzen.
4. Gebruiker markeert eventueel één model als judge-model.

### 3.3 Hoofdflow: golden set maken

1. Gebruiker maakt golden set aan met naam, omschrijving en versie.
2. Gebruiker voegt items handmatig toe of importeert bestand.
3. Per item kiest gebruiker evaluatiemethode en bijbehorende instellingen.
4. Applicatie valideert set vóór uitvoering.

### 3.4 Hoofdflow: evaluatierun

1. Gebruiker kiest golden set en één of meer targetmodellen.
2. Gebruiker controleert geschatte omvang: aantal items × aantal modellen.
3. Gebruiker start run.
4. Applicatie bevriest snapshot van relevante configuratie.
5. Applicatie voert modelcalls uit, slaat responsmetadata op en evalueert elk antwoord.
6. UI toont voortgang, successen en fouten.
7. Na afloop toont applicatie rangschikking en detailresultaten.

## 4. Functionele requirements

### FR-1 Endpointbeheer

Per endpoint worden opgeslagen:

- unieke UUID;
- unieke naam;
- completions-URL, inclusief volledig pad;
- optionele models-URL voor model discovery;
- API-key als geheim;
- configureerbare headernaam waarin API-key wordt geplaatst, bijvoorbeeld `Ocp-Apim-Subscription-Key`;
- configureerbare API-keyprefix, standaard leeg, bijvoorbeeld `Bearer ` inclusief spatie;
- nul of meer extra statische headers als key/value-paren, bijvoorbeeld tenant-ID, project-ID of provider-specifieke headers;
- per extra header een geheim-status; geheime waarden worden versleuteld opgeslagen, gemaskeerd getoond en nooit gelogd of geëxporteerd;
- timeout in seconden, standaard 60;
- maximaal aantal retries, standaard 2;
- optionele limiet voor requests per minuut (RPM);
- optionele limiet voor tokens per minuut (TPM);
- optionele limiet voor gelijktijdige requests;
- actief/inactief-status;
- created/updated timestamps.

Gedrag:

- Backend doet alle externe LLM-calls; browser ontvangt nooit API-keys.
- URL moet standaard HTTPS zijn.
- HTTP is zonder extra bevestiging alleen toegestaan voor `localhost` en loopback-adressen.
- Een niet-lokaal intern HTTP-endpoint kan alleen worden opgeslagen wanneer gebruiker het endpoint expliciet als vertrouwd intern markeert en een securitywaarschuwing bevestigt.
- Redirects naar een ander protocol of host worden niet automatisch gevolgd.
- Headernaam wordt gevalideerd als geldige HTTP-headernaam.
- API-keyprefix is vrije tekst met een korte maximumlengte; lege prefix verstuurt alleen key, bijvoorbeeld voor `Ocp-Apim-Subscription-Key`, en `Bearer ` ondersteunt Authorization-authenticatie.
- Endpoint, API-keyheader, API-keyprefix en alle extra headers zijn via GUI aan te maken en te wijzigen.
- API-key wordt nooit volledig teruggegeven door API of UI; invoer via GUI gebruikt een passwordveld en bestaande waarde wordt alleen gemaskeerd getoond.
- Lege API-key bij update behoudt bestaande waarde.
- Verwijderen wordt geweigerd als historische data afhankelijk is; deactiveren blijft mogelijk.
- Lege rate-limitvelden betekenen dat applicatie voor die dimensie geen limiet afdwingt.
- Rate limits gelden gezamenlijk voor alle target- en judge-calls naar hetzelfde endpoint.
- Een endpoint-limiter reserveert capaciteit vóór verzending. RPM en concurrency worden exact door applicatie bijgehouden. TPM gebruikt provider-usage uit afgeronde responses plus een conservatieve schatting voor nieuwe calls; ontbrekende usage wordt zichtbaar gemarkeerd en kan TPM-handhaving minder exact maken.
- Wachtende calls blijven in runstatus zichtbaar als `rate_limited_wait` en falen niet zolang run niet wordt geannuleerd.
- HTTP 429 gebruikt providerheader `Retry-After` indien aanwezig, anders exponentiële backoff met jitter.
- Model discovery doet een geauthenticeerde `GET` naar models-URL met dezelfde API-keyheader en extra custom headers als completioncalls.
- Als models-URL leeg is, stelt UI een URL voor door afsluitend `/chat/completions` of `/completions` in completions-URL te vervangen door `/models`; gebruiker bevestigt of wijzigt deze vóór gebruik. Geen betrouwbare afleiding betekent geen automatische URL.
- Verwacht discoverycontract is OpenAI-compatible: model-ID's staan in `data[].id`.
- Gebruiker selecteert welke gevonden modellen als modeltestprofiel worden geïmporteerd. Elk nieuw profiel krijgt initieel model-ID als weergavenaam en vereist controle van parameters en prijzen.
- Bestaande profielen worden nooit automatisch gewijzigd of overschreven. Al bestaand model-ID mag opnieuw worden geïmporteerd met een unieke profielnaam.
- Discoveryfouten wijzigen geen configuratie en tonen veilige status zonder secrets of responsebody te loggen.
- Handmatig toevoegen van modellen blijft altijd mogelijk.
- Verbindingstest stuurt een minimale streaming completion naar een gekozen model, valideert protocol en rapporteert bereikbaarheid, TTFT en veilige foutcategorie zonder antwoordinhoud te loggen.

### FR-2 Modelbeheer

Een model hoort bij precies één endpoint en bevat:

- UUID;
- endpoint-ID;
- model-ID zoals verwacht door externe API;
- weergavenaam;
- optionele omschrijving;
- testprofiel met inference-parameters:
  - optionele `temperature`; standaard `0` voor modellen die temperatuur ondersteunen;
  - optionele `reasoning`-instelling voor modellen die reasoning ondersteunen:
    - configureerbare requestveldnaam, bijvoorbeeld `reasoning`, `reasoning_effort` of een genest pad zoals `reasoning.effort`;
    - voorgedefinieerde waarden `off`, `low`, `medium` en `high`;
    - vrije custom waarde voor provider-specifieke varianten;
    - bij `off` is per profiel configureerbaar of waarde letterlijk wordt verzonden of veld volledig wordt weggelaten;
  - optionele `max_tokens`;
  - optionele `top_p`;
  - uitbreidbaar JSON-object voor overige provider-specifieke parameters;
- per parameter kan expliciet worden ingesteld dat deze niet naar het endpoint wordt gestuurd;
- prijsconfiguratie, altijd in EUR:
  - standaardprijs per 1 miljoen inputtokens;
  - standaardprijs per 1 miljoen outputtokens;
  - optionele prijs per 1 miljoen cached inputtokens;
  - optionele prijs per 1 miljoen reasoning tokens;
  - optionele vaste kosten per succesvolle request;
  - optionele contextdrempel `X` in inputtokens;
  - indien drempel actief: hoog-contextprijs per 1 miljoen inputtokens;
  - indien drempel actief: hoog-contextprijs per 1 miljoen outputtokens;
  - optioneel hoog-contexttarief voor cached inputtokens en reasoning tokens;
- actief/inactief-status.

Meerdere modelconfiguraties mogen hetzelfde externe `model-ID` gebruiken zodat temperatuur- en reasoningvarianten onderling getest kunnen worden. Elke configuratie heeft een unieke weergavenaam binnen het endpoint en geldt als afzonderlijk testprofiel. Resultaten tonen zowel extern model-ID als profielnaam.

### FR-3 Extern completion-protocol

MVP ondersteunt OpenAI-compatible `POST` chat completions. Targetmodelcalls gebruiken streaming:

```json
{
  "model": "<model-id>",
  "messages": [
    {"role": "system", "content": "<optional-system-prompt>"},
    {"role": "user", "content": "<prompt>"}
  ],
  "temperature": 0,
  "reasoning": "<configureerbare waarde, alleen indien ondersteund>",
  "stream": true,
  "stream_options": {"include_usage": true}
}
```

Targetrespons is OpenAI-compatible Server-Sent Events (SSE):

- contentfragmenten uit `choices[0].delta.content` worden in volgorde samengevoegd;
- stream eindigt bij `data: [DONE]` of geldige provider-eindindicatie;
- usage wordt indien beschikbaar gelezen uit finaal event: `usage.prompt_tokens`, `usage.completion_tokens`, `usage.total_tokens`;
- optioneel provider-request-ID komt uit responsheader.

Applicatie verwerkt stream incrementeel op backend. Browser hoeft tokens niet live te renderen; UI toont wel actieve status. Een verbroken stream bewaart ontvangen partiële tekst voor diagnose, markeert case als fout en evalueert partiële tekst niet. Judge-calls blijven niet-streaming en gebruiken `choices[0].message.content` voor betrouwbaar JSON-outputcontract.

`temperature` en `reasoning` worden vanuit het gekozen modeltestprofiel opgenomen. Niet-ondersteunde parameters worden weggelaten, niet als `null` verzonden. Reasoning ondersteunt een configureerbare requestveldnaam, inclusief genest veldpad, de standaardwaarden `off`, `low`, `medium`, `high` en een vrije custom waarde.

Buiten configureerbaar reasoningveld en overige expliciete providerparameters blijven requestvelden en responsepaden vast volgens dit OpenAI-compatible contract. Antwoord- en usagepaden zijn in MVP niet configureerbaar.

Onbekende extra velden worden genegeerd. Ontbrekende of ongeldige verplichte velden leveren een duidelijke itemfout op, zonder gehele run te laten crashen.

### FR-4 Golden-setbeheer

Golden set:

- UUID;
- unieke naam;
- omschrijving;
- versielabel als vrije tekst, standaard `1.0`;
- optionele tags;
- created/updated timestamps.

Golden-set-item:

- UUID;
- golden-set-ID;
- stabiele externe sleutel `case_id`, uniek binnen set;
- prompt, verplicht;
- optionele system prompt;
- verwacht antwoord, verplicht voor `exact_match`, optioneel voor `llm_judge`;
- evaluatiemethode;
- evaluatorconfiguratie;
- optionele categorie en tags;
- optioneel gewicht, standaard `1.0`, groter dan `0`;
- ingeschakeld-status;
- vaste sorteervolgorde.

MVP ondersteunt tekstprompts met één optionele system-message en precies één user-message. Multi-turn gesprekken vallen buiten MVP. Datamodel en interne servicegrens moeten latere multi-turn messages toestaan zonder fundamentele migratie van runs en resultaten.

### FR-5 Import en export golden sets

Ondersteunde formaten:

- JSON voor verliesvrije round-trip;
- CSV voor eenvoudige bulkbewerking.

Canoniek JSON-formaat:

```json
{
  "name": "Voorbeeldset",
  "description": "...",
  "version": "1.0",
  "tags": ["support"],
  "items": [
    {
      "case_id": "case-001",
      "prompt": "Wat is 2 + 2?",
      "system_prompt": null,
      "expected_answer": "4",
      "evaluation_method": "exact_match",
      "evaluator_config": {
        "trim": true,
        "case_sensitive": true,
        "normalize_newlines": true
      },
      "category": "rekenen",
      "tags": ["basis"],
      "weight": 1.0,
      "enabled": true
    }
  ]
}
```

CSV-kolommen:

`case_id,prompt,system_prompt,expected_answer,evaluation_method,evaluator_config,category,tags,weight,enabled`

JSON-objecten/lijsten in CSV worden als JSON-string opgeslagen. Import toont validatiefouten met rij/case-ID en importeert niets wanneer één of meer rijen ongeldig zijn (atomair).

### FR-6 Exact-match-evaluator

Configuratie per item:

- `trim`: verwijder voor/achterliggende whitespace, standaard `true`;
- `case_sensitive`: standaard `true`;
- `normalize_newlines`: converteer CRLF/CR naar LF, standaard `true`;
- `collapse_whitespace`: standaard `false`.

Normalisatie wordt in vaste volgorde uitgevoerd: newlines normaliseren, trimmen, whitespace eventueel inklappen, case eventueel normaliseren. Daarna volledige stringvergelijking.

Output:

- `score`: `1.0` bij gelijk, anders `0.0`;
- `passed`: `score == 1.0`;
- korte reden met wel/geen match, zonder geheime data;
- genormaliseerde verwachte en werkelijke waarden worden in resultaatmetadata bewaard voor verklaarbaarheid;

### FR-7 Aanvullende deterministische evaluatoren

Alle deterministische evaluatoren retourneren `score` (`0.0` of `1.0`), `passed`, een korte reden en methode-specifieke metadata. Een configuratiefout wordt vóór een run door golden-setvalidatie gemeld en niet als model failure geteld.

#### `contains`

Controleert of één of meer verwachte tekstfragmenten in het volledige kandidaatantwoord voorkomen.

Configuratie:

- `expected_fragments`: niet-lege lijst met strings;
- `match_mode`: `all` of `any`, standaard `all`;
- `case_sensitive`: standaard `true`;
- `normalize_newlines`: standaard `true`;
- `collapse_whitespace`: standaard `false`.

`passed = true` wanneer volgens `match_mode` alle of minstens één genormaliseerd fragment voorkomt. Lege fragmenten zijn ongeldig.

#### `regex`

Controleert kandidaatantwoord met een reguliere expressie.

Configuratie:

- `pattern`: verplicht, niet leeg;
- `full_match`: standaard `false`; bij `false` wordt gezocht, bij `true` moet volledig antwoord matchen;
- `case_sensitive`: standaard `true`;
- `multiline`: standaard `false`;
- `dot_all`: standaard `false`.

Applicatie compileert patroon tijdens validatie. Regex-uitvoering krijgt een veilige timeout om catastrophic backtracking te begrenzen. Ongeldig patroon blokkeert runstart.

#### `numeric`

Haalt numerieke waarde uit kandidaatantwoord en vergelijkt deze met verwacht getal.

Configuratie:

- `expected_value`: verplicht decimaal getal;
- `absolute_tolerance`: optioneel niet-negatief decimaal getal;
- `relative_tolerance_percent`: optioneel niet-negatief decimaal percentage;
- `extraction`: `entire_answer` of `first_number`, standaard `entire_answer`;
- `decimal_separator`: `.` of `,`, standaard `.`;
- `allow_thousands_separator`: standaard `false`.

Minstens één tolerantie wordt opgegeven; zonder tolerantie geldt exact numeriek gelijk. Bij beide toleranties slaagt antwoord wanneer verschil binnen minstens één tolerantie valt. Berekening gebruikt decimalen, niet binary floats. Geen of meerdere ambigue waarden bij `entire_answer` leveren `passed = false` met reden, geen systeemfout.

#### `json_schema`

Parseert kandidaatantwoord als JSON en valideert tegen configureerbaar JSON Schema.

Configuratie:

- `schema`: verplicht geldig JSON Schema-object;
- `allow_markdown_code_fence`: standaard `true`;
- `schema_draft`: standaard Draft 2020-12;
- optioneel `expected_value` voor aanvullende diepe gelijkheid, standaard afwezig.

Schema zelf wordt bij golden-setvalidatie gecontroleerd. Kandidaat met ongeldige JSON of schema-afwijkingen krijgt `score = 0.0` en compacte validatiereden. Codefence mag defensief worden verwijderd; overige omringende tekst is niet toegestaan.

### FR-8 LLM-as-a-judge-evaluator

#### Configuratie

Judge-configuratie volgt vaste hiërarchie:

1. één globaal standaard-judgeprofiel;
2. per golden set optioneel ander judgeprofiel;
3. per run optioneel een override van het judgeprofiel;
4. per item geen modeloverride: alleen rubric, threshold en context.

Een judgeprofiel bevat:

- judge-endpoint en judge-modeltestprofiel;
- judge-inference-parameters, standaard met `temperature = 0`;
- judge-prompttemplate met versie;
- standaard pass threshold, `0.7`;
- standaardrubric met vijf criteria en gelijke gewichten:
  1. `factual_correctness`: feitelijke juistheid;
  2. `completeness`: volledigheid;
  3. `relevance`: relevantie;
  4. `instruction_compliance`: naleving van expliciete instructies;
  5. `consistency`: geen tegenstrijdigheden met verwacht antwoord of rubric;
- maximaal één retry bij ongeldige judge-output.

Per item:

- optioneel verwacht antwoord;
- optionele itemrubric die standaardcriteria aanvult of volledig vervangt; ieder criterium heeft stabiele `id`, omschrijving en positief gewicht;
- als verwacht antwoord ontbreekt, moet rubric voldoende zelfstandige beoordelingscriteria bevatten;
- optionele item-specifieke pass threshold;
- optioneel extra context;
- geen keuze voor ander judge-model.

Targetmodel mag ook judge zijn. UI waarschuwt voor mogelijke self-judging bias wanneer targetmodel en judge-model gelijk zijn.

#### Judge-input

Judge ontvangt ondubbelzinnig gescheiden:

- originele prompt;
- verwacht antwoord, indien aanwezig;
- rubric/criteria;
- kandidaatantwoord.

Template instrueert judge prompt-injecties in kandidaatantwoord te negeren en uitsluitend als data te behandelen.

#### Verplicht judge-outputcontract

```json
{
  "score": 0.82,
  "passed": true,
  "reason": "Korte feitelijke totaaltoelichting",
  "criteria": [
    {
      "id": "factual_correctness",
      "score": 0.9,
      "reason": "Feiten komen overeen met referentie."
    },
    {
      "id": "completeness",
      "score": 0.7,
      "reason": "Eén ondersteunend detail ontbreekt."
    }
  ]
}
```

- Judge retourneert precies één subresultaat voor elk toepasselijk rubriccriterium en geen onbekende criterium-ID's.
- Iedere criteriumscore is een getal tussen `0.0` en `1.0` en heeft een korte reden.
- Applicatie berekent leidende totaalscore zelf als gewogen gemiddelde: `sum(criteriumscore × gewicht) / sum(gewicht)`.
- Door judge geretourneerde `score` wordt ter controle opgeslagen maar is niet leidend. Afwijking groter dan `0.01` wordt gemarkeerd in auditmetadata, zonder resultaat te verwerpen.
- Applicatie bepaalt `passed` zelf uit berekende totaalscore en threshold; door judge geretourneerde `passed` wordt ter controle gelezen maar is niet leidend.
- Subscores, subredenen, gewichten, berekende totaalscore en ruwe judge-output worden opgeslagen en in itemdetail en exports getoond.
- JSON in Markdown-codefence mag defensief worden uitgepakt. Ontbrekende, dubbele of onbekende criteria en overige ongeldige output worden eenmaal opnieuw gevraagd en eindigen daarna als evaluatiefout.
- Judge-calltokengebruik, latency en kosten worden apart opgeslagen en niet vermengd met kosten van targetmodel.
- Ruwe judge-output en prompttemplateversie worden opgeslagen voor auditbaarheid.

### FR-9 Runconfiguratie en uitvoering

Run bevat:

- naam, automatisch voorstel met datum/tijd, handmatig wijzigbaar;
- golden set;
- één of meer targetmodeltestprofielen;
- optionele override van het judgeprofiel dat vanuit de golden set of globale standaard wordt geërfd;
- optionele runnotitie;
- concurrency, globaal configureerbaar, standaard 4;
- snapshots van golden-set-items, modelinstellingen, prijzen, endpoint-URL/headernaam (nooit geheim), evaluatorconfiguratie, judge-template en ingestelde quality gates.

Statussen:

`pending`, `running`, `completed`, `completed_with_errors`, `failed`, `cancelled`.

Item/model-uitvoering:

1. target completion aanroepen;
2. antwoord en metadata opslaan;
3. gekozen evaluator uitvoeren;
4. resultaat opslaan;
5. aggregaten bijwerken.

Eisen:

- Uitvoering gebeurt via een duurzame SQLite-jobqueue zodat paginaverversing en applicatieherstart een run niet verliezen.
- Elke case/model-combinatie is een afzonderlijke persistente job met status, pogingnummer, lease-eigenaar en lease-expiratietijd.
- Worker claimt jobs atomair met een tijdgebonden lease; verlopen leases worden na startup opnieuw beschikbaar gemaakt.
- Na serverrestart worden runs met status `running` automatisch hervat. Reeds succesvol afgeronde of definitief gefaalde case/model-combinaties worden niet opnieuw uitgevoerd; alleen `pending`, wachtende en onderbroken jobs gaan verder.
- Een externe call die tijdens crash actief was kan niet betrouwbaar worden hervat of bij provider worden geannuleerd. Bij verlopen lease wordt die job opnieuw uitgevoerd. Dit kan dubbele providerkosten veroorzaken; resultaat krijgt `recovered_after_interruption` en UI/log toont waarschuwing met execution/test-ID.
- Resultaatpersistentiestatus en jobafronding gebeuren in één databasetransactie om lokale dubbeltelling te voorkomen.
- Run kan worden geannuleerd; actieve HTTP-call mag afronden, nog niet gestarte calls worden overgeslagen.
- Retry alleen op timeouts, netwerkfouten, HTTP 429 en HTTP 5xx, met exponentiële backoff en jitter.
- Geen retry op overige HTTP 4xx.
- Elke poging en definitieve foutcategorie wordt geregistreerd.
- Startup voert queue-recovery uit vóór workers starten en registreert aantal hervatte runs en jobs zonder prompt- of antwoordinhoud te loggen.
- Responses worden niet gecachet: elke run meet nieuwe completions.

### FR-10 Metrics en aggregatie

#### Per modelantwoord

- score `0.0–1.0`;
- passed/failed/error;
- target total latency in milliseconden, gemeten van vlak vóór requestverzending tot compleet ontvangen stream;
- time to first token (`TTFT`) in milliseconden, gemeten van vlak vóór requestverzending tot eerste niet-lege contentdelta;
- generation duration in seconden, van eerste niet-lege contentdelta tot compleet ontvangen stream;
- tokens per second (`TPS`) = outputtokens / generation duration;
- input-, output- en totaaltokens, indien provider rapporteert;
- targetkosten;
- judge latency, tokens en kosten indien van toepassing;
- fouttype en foutmelding;
- aantal pogingen.

#### Per model binnen run

- aantal totaal, voltooid, passed, failed en error;
- pass rate = `passed / (passed + failed)`; errors staan apart;
- gemiddelde gewogen score = `sum(score × weight) / sum(weight)` over succesvol geëvalueerde cases;
- error rate = `errors / totaal`;
- gemiddelde, mediaan en p95 targetlatency;
- gemiddelde, mediaan en p95 TTFT;
- gemiddelde, mediaan en p95 TPS;
- som input-, output-, cached-input- en reasoningtokens;
- totale targetkosten;
- totale judgekosten;
- totale runkosten = targetkosten + toegerekende judgekosten;
- kosten per succesvol geëvalueerde case;
- kosten per passed case.

Resultaten tonen altijd steekproefgrootte en error count om misleidende vergelijkingen te voorkomen. Rangschikking is op hoogste gewogen score, daarna laagste error rate, daarna laagste totale kosten. TTFT of TPS is `unknown` wanneer geen contentdelta, outputtokengebruik of geldige positieve generation duration beschikbaar is; applicatie schat geen tokenaantallen. Voor zeer korte generatie waarbij eerste en laatste chunk samenvallen is TPS `unknown` om deling door nul en misleidende waarden te voorkomen.

#### Kostenformule

Alle kosten zijn uitsluitend in EUR. Elk modeltestprofiel heeft standaard input- en outputprijzen en kan daarnaast optionele tarieven bevatten voor cached inputtokens, reasoning tokens en vaste kosten per succesvolle request. Optioneel heeft het profiel één contextdrempel `X`, bepaald door het aantal input-/prompttokens van de request:

- `input_tokens <= X`: gebruik standaard input- en outputprijzen;
- `input_tokens > X`: gebruik hoog-context input- en outputprijzen voor de volledige request.

De drempel is dus niet staffelgewijs: wanneer context groter is dan `X`, worden alle input- en outputtokens van die request tegen het hoog-contexttarief berekend. Gelijkheid valt onder het standaardtarief.

Cached inputtokens zijn een subset van inputtokens. Reasoning tokens zijn een subset van outputtokens. Ze worden nooit dubbel belast:

```text
regular_input_tokens  = input_tokens  - cached_input_tokens
regular_output_tokens = output_tokens - reasoning_tokens

selected_prices = high_context_prices if input_tokens > context_threshold
                  else standard_prices

input_cost     = regular_input_tokens  / 1_000_000 × selected_input_price
cached_cost    = cached_input_tokens   / 1_000_000 × selected_cached_price
output_cost    = regular_output_tokens / 1_000_000 × selected_output_price
reasoning_cost = reasoning_tokens      / 1_000_000 × selected_reasoning_price
total_cost     = input_cost + cached_cost + output_cost + reasoning_cost
                 + fixed_cost_per_successful_request
```

Regels:

- Zonder apart cached tarief worden cached tokens tegen gekozen reguliere inputprijs berekend.
- Zonder apart reasoningtarief worden reasoning tokens tegen gekozen reguliere outputprijs berekend.
- Bij actieve contextdrempel zijn hoog-context input- en outputprijzen verplicht. Hoog-context cached- en reasoningtarieven zijn optioneel; ontbreken ze, dan valt elk terug op respectievelijk regulier hoog-context input- of outputtarief.
- Vaste requestkosten worden eenmaal gerekend voor iedere providercall met geldige completionrespons, inclusief target- en judge-call. Mislukte HTTP-/parsepogingen krijgen geen vaste requestkosten, tenzij provider toch usage rapporteert; dan wordt providerusage en vaste requestkosten wel geregistreerd.
- Retries met een geldige completionrespons worden als afzonderlijke betaalde calls geregistreerd.
- Applicatie leest cached en reasoning usage uit OpenAI-compatible detailvelden indien aanwezig, waaronder `usage.prompt_tokens_details.cached_tokens` en `usage.completion_tokens_details.reasoning_tokens`. Ontbrekende detailvelden betekenen `unknown`, niet automatisch nul, tenzij provider expliciet nul rapporteert.
- Als subtotaal-tokenvelden bekend zijn, valideert applicatie dat subsets niet groter zijn dan totalen; ongeldige usage maakt kostenstatus `unknown`.

Contextdrempel en beide hoog-context basisprijzen zijn samen optioneel en worden als één configuratie gevalideerd. Een gedeeltelijk ingevulde staffel is ongeldig. Alle prijzen moeten niet-negatief zijn. Berekening gebruikt decimalen, geen binary floats. Opslag met minimaal 8 decimalen; weergave adaptief afgerond. Als benodigde usage ontbreekt, is kostenstatus `unknown`; applicatie raadt tokens of kosten niet.

### FR-11 Resultateninterface

Runoverzicht toont:

- status en voortgang;
- start/eindtijd en duur;
- golden-setversie en modellen;
- vergelijkende tabel met kernmetrics;
- sorteren op score, pass rate, latency, TTFT, TPS en kosten;
- filters op model, methode, categorie, tag, pass/fail/error;
- itemdetail met prompt, verwacht antwoord, kandidaatantwoord, score, reden, total latency, TTFT, TPS, tokens, kosten en foutdetails;
- indicatie wanneer metric onbekend is.

Grafieken zijn nuttig maar niet verplicht voor MVP. Tabellen moeten alle vereiste informatie bevatten.

### FR-12 Configuratie-import en -export

Alle functionele configuratie is via GUI te beheren en verliesvrij als één versiegebonden JSON-bestand te exporteren/importeren:

- endpoints, API-keyheader, API-keyprefix, models-URL, rate limits en niet-geheime extra headers;
- geheime headerdefinities en optionele environmentvariabele-referenties, maar nooit secretwaarden;
- modeltestprofielen, inferenceparameters en EUR-prijzen;
- golden sets, items en evaluatorconfiguratie;
- judgeprofielen, templates en rubrics;
- quality-gatedefaults.

Canoniek topniveau:

```json
{
  "schema_version": "1.0",
  "exported_at": "2026-01-01T12:00:00Z",
  "endpoints": [],
  "model_profiles": [],
  "judge_profiles": [],
  "golden_sets": []
}
```

Secretvelden worden als volgt geëxporteerd:

```json
{
  "header_name": "Authorization",
  "prefix": "Bearer ",
  "secret": true,
  "value": null,
  "env_var": "PROVIDER_API_KEY"
}
```

- `value` van een secret is altijd `null`; zonder `env_var` moet gebruiker secret na import via GUI invoeren.
- Niet-geheime custom headers mogen hun waarde wel in JSON bevatten.
- Import valideert `schema_version`, referenties, unieke namen, volledige configuratie en limieten vóór schrijven.
- UI toont preview met aantallen, waarschuwingen, ontbrekende secrets en conflicten.
- Gebruiker kiest `merge` of `replace`. `replace` vereist expliciete bevestiging en verwijdert geen historische runs; gebruikte records worden gearchiveerd.
- Import is atomair. Een validatiefout wijzigt niets.
- Geïmporteerde objecten bewaren stabiele UUID's waar veilig; conflicten worden volgens preview gemapt naar nieuwe UUID's.
- Export van configuratie staat los van resultaat- en golden-setexports.

Operationele applicatie-instellingen zijn via settings-GUI én `.env` instelbaar. `.env` is leidend: een aanwezige environmentwaarde overschrijft databasewaarde en wordt in GUI als read-only override met bronnaam getoond. Zonder environmentwaarde geldt GUI/databasewaarde; ontbreekt die ook, dan geldt gedocumenteerde default. Secrets kunnen via GUI of environmentreferentie worden geleverd, maar worden nooit door gewone JSON-export meegenomen.

### FR-13 Resultatenexport

- JSON-export bevat runmetadata, snapshots, alle itemresultaten, ruwe antwoorden, metrics en fouten; geheimen uitgesloten.
- CSV-export bevat één rij per case/model-combinatie en vlakke kernvelden.
- Zelfstandig HTML-rapport bevat inline styling, runmetadata, modelvergelijking, aggregaten en itemresultaten. Het heeft geen externe assets, scripts, API-calls of netwerkverbinding nodig en blijft na export offline leesbaar.
- HTML heeft print-CSS zodat gebruiker via browser een PDF kan maken. Native PDF-generatie valt buiten MVP.
- Gevoelige modelinhoud wordt correct HTML-escaped; Content Security Policy verhindert uitvoering van geïnjecteerde content.
- Bestandsnaam bevat runnaam en ISO-datum.
- Export volgt actieve UI-filters niet en exporteert altijd volledige run.

### FR-14 Quality gates

Quality gates zijn optioneel configureerbaar per run en kunnen als defaults bij een golden set worden opgeslagen. Runoverride vervangt golden-setdefaults.

Ondersteunde gates:

- minimale gewogen gemiddelde score;
- minimale pass rate;
- maximaal foutpercentage;
- maximale gemiddelde total latency;
- maximale p95 total latency;
- maximale p95 TTFT;
- minimale gemiddelde TPS;
- maximale totale kosten in EUR.

Score-, pass-rate-, error-, latency-, TTFT- en TPS-gates worden per geselecteerd targetmodelprofiel beoordeeld: ieder profiel moet elke ingestelde gate halen. Maximale totale kosten geldt standaard voor de volledige run, inclusief target- en judgekosten. UI toont per gate en model de gemeten waarde, grens en uitkomst.

Naast uitvoeringsstatus heeft run een gate-uitkomst:

- `not_evaluated`: geen gates ingesteld of run nog niet terminaal;
- `passed`: alle ingestelde gates zijn gehaald;
- `failed`: minstens één gate is niet gehaald;
- `indeterminate`: vereiste metric is `unknown`, bijvoorbeeld ontbrekende usage voor kosten/TPS.

`indeterminate` telt standaard als niet geslaagd voor automatisering en wordt afzonderlijk van `failed` getoond. Gates worden pas definitief geëvalueerd bij `completed` of `completed_with_errors`; een geannuleerde of technisch gefaalde run krijgt `not_evaluated`. Decimalen en percentages worden exact gevalideerd. Grens is inclusief: minimum slaagt bij `waarde >= grens`, maximum bij `waarde <= grens`.

### FR-15 Historie en beheer

- Runs zijn chronologisch doorzoekbaar op naam, golden set en model.
- Historische run blijft interpreteerbaar nadat actuele configuratie wijzigt door snapshots.
- Run verwijderen vereist bevestiging en verwijdert afhankelijke itemresultaten atomair.
- Automatische retentie is configureerbaar via `RUN_RETENTION_DAYS`; leeg of `0` betekent onbeperkt bewaren en is standaard.
- Bij positieve retentie verwijdert periodieke cleanup alleen voltooide, gefaalde en geannuleerde runs waarvan eindtijd buiten retentie valt, inclusief afhankelijke snapshots en resultaten, in een transactie.
- UI toont geplande vervaldatum van runs wanneer retentie actief is.
- Resultatenexport toont vóór download een waarschuwing dat bestand gevoelige prompts en antwoorden kan bevatten.
- Golden sets, endpoints en modellen met historische verwijzingen worden gedeactiveerd of gearchiveerd, niet hard verwijderd.

## 5. Niet-functionele requirements

### NFR-1 Architectuur en stack

Conform `agent.md`:

- backend: Python 3.12+, Flask, Pydantic;
- database: SQLite, inclusief duurzame jobqueue en workerleases;
- frontend: HTML, CSS en TypeScript/JavaScript;
- LLM-verkeer uitsluitend server-side via OpenAI-compatible endpoints;
- pytest voor backendtests.

Implementatie gebruikt server-rendered Flask UI met lichte TypeScript/JavaScript-interactiviteit en REST-JSON endpoints. Geen zwaar frontendframework. Backendstructuur scheidt routes, services, evaluators, providerclient, repositories en domeinmodellen.

### NFR-2 Deployment en configuratie

Ondersteunde startmethoden:

1. primair: Docker Compose;
2. alternatief: rechtstreeks via Python `.venv` op Windows en Linux.

Docker Compose:

- bouwt applicatie uit repository;
- publiceert alleen configureerbare lokale hostpoort;
- mount persistente volumes voor SQLite-data en exports;
- leest configuratie en secrets uit lokale `.env`;
- bevat healthcheck;
- vereist geen externe database of jobqueue voor MVP.

Rechtstreeks starten gebruikt dezelfde configuratie, database en applicatiecode. `README.md` documenteert commando's afzonderlijk voor PowerShell en een POSIX-shell.

Configuratie:

`.env` wordt geladen met `python-dotenv` en `override=True`. Project levert zowel `.env` als `.env.example`; echte secrets staan alleen in lokale `.env`, die in `.gitignore` staat.

Applicatieconfiguratie omvat minimaal:

```dotenv
FLASK_ENV=development
SECRET_KEY=change-me
DATABASE_PATH=./data/evaluator.db
HOST=127.0.0.1
PORT=5000
DEFAULT_REQUEST_TIMEOUT_SECONDS=60
DEFAULT_MAX_RETRIES=2
RUN_CONCURRENCY=4
LOG_LEVEL=INFO
RUN_RETENTION_DAYS=0
MAX_PROMPT_CHARS=1000000
MAX_SYSTEM_PROMPT_CHARS=100000
MAX_EXPECTED_ANSWER_CHARS=1000000
MAX_MODEL_ANSWER_CHARS=10000000
MAX_JUDGE_RUBRIC_CONTEXT_CHARS=100000
MAX_IMPORT_FILE_BYTES=104857600
MAX_PROVIDER_RESPONSE_BYTES=104857600
ENCRYPTION_KEY=change-me
```

Functionele configuratie wordt via GUI/database beheerd en kan via JSON worden geïmporteerd/geëxporteerd. Dit omvat endpoints en headers. Operationele instellingen worden via settings-GUI/database en `.env` beheerd; `.env`-waarden hebben voorrang en hun actieve bron is zichtbaar in GUI. API-keys worden via GUI of environmentreferentie ingesteld en versleuteld at rest met `ENCRYPTION_KEY`; sleutel zelf staat alleen in `.env`.

### NFR-3 Security

- Secrets nooit loggen, exporteren of naar frontend sturen.
- API-keys en als geheim gemarkeerde custom headers worden versleuteld in SQLite.
- Prompts, verwachte antwoorden, modelantwoorden, judge-input en judge-output worden onversleuteld in lokale SQLite opgeslagen.
- Prompt- en antwoordinhoud wordt nooit in applicatie-, access- of foutlogs opgenomen.
- Logs verwijzen waar nodig uitsluitend naar UUID's zoals run-ID, case-ID en een uniek execution/test-ID.
- Externe foutresponses worden vóór logging geschoond; responsebody wordt standaard niet gelogd omdat die invoer of modelinhoud kan herhalen.
- Logberichten en externe foutteksten worden op bekende secretwaarden geredigeerd.
- Externe responsbody wordt niet als HTML geïnjecteerd; UI escaped alle modelcontent.
- CSRF-bescherming op muterende browserrequests.
- Invoerlengtes en responsegroottes zijn begrensd met via `.env` aanpasbare defaults:
  - prompt: 1.000.000 tekens;
  - system prompt: 100.000 tekens;
  - verwacht antwoord: 1.000.000 tekens;
  - modelantwoord: 10.000.000 tekens;
  - judge-rubric plus context: 100.000 tekens;
  - importbestand: 100 MiB (`104857600` bytes);
  - providerresponse of volledige stream: 100 MiB (`104857600` bytes).
- Limieten worden vóór verwerking afgedwongen. Een overschrijding stopt gecontroleerd met validatie- of providerresponsefout; gedeeltelijke oversized streams worden niet geëvalueerd. Startup valideert dat alle limieten positieve gehele getallen zijn.
- Server-side URL-validatie beperkt SSRF-risico. HTTPS is standaard verplicht; HTTP is alleen toegestaan voor localhost/loopback of een expliciet als vertrouwd gemarkeerd intern endpoint. Redirects naar andere hosts/protocollen worden niet automatisch gevolgd.
- Dependencies worden vastgelegd in `requirements.txt`.

### NFR-4 Betrouwbaarheid

- SQLite foreign keys ingeschakeld.
- Mutaties en imports transactioneel.
- HTTP-calls hebben altijd timeout.
- Runfout bij één case beïnvloedt overige cases niet.
- Database ondersteunt consistente schema-migraties.
- Applicatie toont bruikbare foutmelding; stacktrace alleen in developmentlog.

### NFR-5 Performance

MVP-defaults zijn maximaal 10.000 items per golden set, 20 modeltestprofielen per run, standaard 4 gelijktijdige externe calls en een configureerbaar maximum van 10 gelijktijdige calls. Dit zijn geen hardcoded constanten: beheerders kunnen ze eenvoudig via `.env` aanpassen zonder codewijziging of rebuild.

Configuratievariabelen:

```dotenv
MAX_GOLDEN_SET_ITEMS=10000
MAX_MODEL_PROFILES_PER_RUN=20
DEFAULT_RUN_CONCURRENCY=4
MAX_RUN_CONCURRENCY=10
```

Applicatie valideert bij startup dat waarden positieve gehele getallen zijn en dat standaard-concurrency niet groter is dan maximum-concurrency. Actieve grenzen worden in relevante UI-formulieren getoond. Verhoging kan impact hebben op geheugen, provider-rate-limits en SQLite-writeconcurrency.

- lijstpagina's zijn gepagineerd;
- UI blijft responsief tijdens run;
- runstatus wordt maximaal elke 2 seconden gepolld;
- dashboard voor 10.000 resultaatrijen laadt binnen 3 seconden exclusief eerste zware aggregatie op referentiehardware.

### NFR-6 Audit en reproduceerbaarheid

Elke run bewaart:

- UTC timestamps in ISO 8601;
- applicatieversie/git-commit indien beschikbaar;
- immutable snapshots van gebruikte invoer en configuratie;
- judge-template en versie;
- EUR-prijzen, cached/reasoning-/vaste-requesttarieven, optionele contextdrempel en gekozen prijstarief op moment van run;
- ruwe model- en judge-antwoorden;
- fout- en retrymetadata.

Niet reproduceerbare providerfactoren, zoals stil gewijzigde modelweights, worden expliciet niet gegarandeerd.

### NFR-7 Logging

Python `logging`, met timestamp, level, component, run-ID, case-ID en uniek execution/test-ID waar relevant. Geen `print`. Prompts, verwachte antwoorden, modelantwoorden, judge-input en judge-output worden nooit geheel of gedeeltelijk gelogd; zij staan onversleuteld in lokale SQLite. Ook bij exceptions en externe providerfouten worden request- en responsebody niet gelogd.

### NFR-8 Taal, toegankelijkheid en browserondersteuning

- MVP-interface is uitsluitend Nederlands en heeft geen taalschakelaar.
- Code, identifiers, API-velden, logs en technische documentatie zijn Engels.
- Gebruikersteksten staan centraal beheerd en niet verspreid hardcoded, zodat latere internationalisatie mogelijk blijft zonder domeinlogica te wijzigen.
- Toetsenbordbediening, zichtbare focus, labels op invoervelden en voldoende contrast.
- Laatste twee versies van Chrome, Edge en Firefox.
- Desktop-first; bruikbaar vanaf 1024 px breed.

## 6. Conceptueel datamodel

| Entiteit | Belangrijkste relaties |
|---|---|
| `Endpoint` | heeft meerdere `Model`-records |
| `EndpointSecret` | hoort bij één `Endpoint`; versleutelde API-key/secret headers |
| `Model` | hoort bij `Endpoint`; gebruikt door `RunModelSnapshot` |
| `GoldenSet` | heeft meerdere `GoldenSetItem`-records |
| `GoldenSetItem` | bevat evaluatorconfiguratie |
| `JudgeConfiguration` | verwijst naar judge-model en templateversie |
| `Run` | verwijst naar golden set en bevat snapshots/status |
| `RunCaseSnapshot` | immutable kopie van golden-set-item per run |
| `RunModelSnapshot` | immutable kopie van model, endpointmetadata en prijzen |
| `EvaluationResult` | één per run-case/model; targetrespons plus evaluatie |
| `RequestAttempt` | nul of meer per target- of judge-call |
| `RunJob` | duurzame queuejob per run-case/model-combinatie, met status, lease en herstelmetadata |

Belangrijke constraints:

- uniek: `(golden_set_id, case_id)`;
- uniek: `(endpoint_id, display_name)`; meerdere testprofielen mogen hetzelfde `model_id` gebruiken;
- uniek: `(run_id, run_case_snapshot_id, run_model_snapshot_id)`;
- secrets worden nooit opgenomen in snapshots;
- bedragen als decimal/string of vaste decimal databasekolom, niet float;
- JSON-configuratie wordt bij lezen met Pydantic gevalideerd.

Definitief fysiek schema en migraties worden tijdens implementatie afgeleid uit dit model.

## 7. Backend-API (MVP-contract op hoofdlijnen)

Alle endpoints onder `/api/v1`, JSON tenzij bestanddownload.

- `GET/POST /endpoints`
- `GET/PATCH/DELETE /endpoints/{id}`
- `POST /endpoints/{id}/test`
- `GET/POST /endpoints/{id}/models`
- `POST /endpoints/{id}/discover-models`
- `GET/PATCH/DELETE /models/{id}`
- `GET/POST /golden-sets`
- `GET/PATCH/DELETE /golden-sets/{id}`
- `POST /golden-sets/import`
- `GET /golden-sets/{id}/export?format=json|csv`
- `GET/POST /golden-sets/{id}/items`
- `GET/PATCH/DELETE /golden-set-items/{id}`
- `GET/PATCH /judge-configuration`
- `POST /configuration/import`
- `GET /configuration/export`
- `GET/PATCH /settings`
- `GET/POST /runs` (inclusief optionele quality gates)
- `GET /runs/{id}`
- `POST /runs/{id}/cancel`
- `POST /runs/{id}/rerun-failures`
- `GET /runs/{id}/results`
- `GET /runs/{id}/export?format=json|csv|html`

Fouten volgen consistent contract:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Leesbare samenvatting",
    "details": [{"field": "name", "message": "Verplicht"}],
    "request_id": "uuid"
  }
}
```

Routes krijgen docstrings en backend levert een actuele OpenAPI-specificatie die in CI/tests op geldigheid en routeconsistentie wordt gecontroleerd.

## 8. Schermen

1. **Dashboard:** recente runs, status en kernvergelijkingen.
2. **Endpoints:** lijst, toevoegen/bewerken, API-keyheader, prefix en extra headers beheren, key vervangen, environmentreferentie instellen en verbinding testen.
3. **Modellen:** modellen per endpoint, parameters en prijzen.
4. **Golden sets:** lijst, details, import/export en itemeditor.
5. **Judge-configuratie:** model, template, threshold en testmogelijkheid.
6. **Nieuwe run:** golden set, modellen, samenvatting en startbevestiging.
7. **Run detail:** live voortgang, vergelijking, filters en itemdetails.
8. **Runhistorie:** zoeken, openen en verwijderen.
9. **Configuratie:** volledige JSON-import/export met preview, merge/replace en ontbrekende-secretstatus.
10. **Instellingen:** operationele waarden bewerken en per waarde bron tonen (`GUI/database`, `.env override` of `default`).

Destructieve acties vragen expliciete bevestiging.

## 9. Validatie en foutcategorieën

Gestandaardiseerde foutcategorieën:

- `validation_error`;
- `authentication_error` (extern 401/403);
- `rate_limit_error` (429);
- `provider_client_error` (overige 4xx);
- `provider_server_error` (5xx);
- `timeout_error`;
- `network_error`;
- `invalid_provider_response`;
- `judge_parse_error`;
- `cancelled`;
- `internal_error`.

UI toont veilige fouttekst plus voldoende context om configuratie te herstellen.

## 10. Teststrategie

### Unit tests

- exact-matchnormalisatie en alle opties;
- contains met `all`/`any` en normalisatie;
- regexflags, search/full-match, ongeldige patronen en timeout;
- numerieke parsing, decimal separators en absolute/relatieve toleranties;
- JSON-parsing, codefences, schemavalidatie en optionele diepe gelijkheid;
- judge-outputparser, volledige criteriumset, subscorevalidatie, gewogen totaalscore, afwijkende judge-totaalscore en thresholdlogica;
- kostenberekening, EUR-afronding, ontbrekende usage, cached/reasoning-subsets zonder dubbeltelling, vaste requestkosten en selectie van standaard- of hoog-contexttarief op grenswaarden `X` en `X + 1`;
- aggregatie, gewichten, errors en percentielen;
- quality-gateevaluatie op inclusieve grenzen, per-modelmetrics, totale kosten en unknown/indeterminate gevallen;
- SSE-parser met gefragmenteerde events, `[DONE]`, finale usage, verbroken streams en partiële tekst;
- monotone tijdmeting en berekening van total latency, TTFT en TPS inclusief unknown-randgevallen;
- validatie van URL, headers, prijzen, import en alle configureerbare invoer-/responsegroottelimieten;
- encryptie/decryptie en redactie van secrets;
- logredactie: prompts, antwoorden en providerbodies verschijnen ook bij fouten niet in logs; correlation via execution/test-ID;
- retryclassificatie en backoff zonder echte wachttijd;
- endpointlimiter voor RPM, TPM, concurrency en `Retry-After`.

### Integratietests

- CRUD en databaseconstraints;
- JSON/CSV round-trip;
- mocked OpenAI-compatible target-, judge- en model-discovery-endpoints;
- model discovery, URL-voorstel, selectieve import en behoud van bestaande profielen;
- succesvolle run met meerdere modellen/evaluatoren;
- gedeeltelijke fouten, retries en annulering;
- restart-recovery met verlopen leases, behoud van afgeronde jobs en hervatting van onafgeronde jobs;
- transactionele resultaatopslag plus jobafronding voorkomt lokale dubbele resultaten;
- snapshots blijven gelijk na configuratiewijziging;
- configuratie-export/import round-trip, merge/replace, conflictmapping, schema-versies en ontbrekende secrets;
- endpoint- en custom-headerbeheer via GUI en JSON, zonder secretwaarden in export;
- settingsprioriteit `.env > GUI/database > default` en read-only weergave van overrides;
- JSON-, CSV- en HTML-exports bevatten data maar geen secrets; HTML is offline, escaped en printbaar;
- retentiecleanup verwijdert alleen verlopen terminale runs en alle afhankelijke data atomair; `0` bewaart onbeperkt;

### End-to-end rooktest

Configureer twee mocked modellen, importeer fixture-set, voer run uit en controleer dashboard/export.

Testsuite moet zonder echte API-key of netwerktoegang slagen.

## 11. Oplevercriteria

Implementatie is gereed wanneer:

- alle MVP-requirements zijn geïmplementeerd;
- `pytest` volledig slaagt;
- frontend build/lint/typecheck slaagt indien van toepassing;
- database kan worden geïnitialiseerd en gemigreerd;
- `.venv/`, `.env`, database en gegenereerde exports zijn genegeerd door git;
- `.env.example` bevat alle variabelen zonder echte secrets;
- lokale `.env` bestaat conform `agent.md`;
- `README.md` beschrijft installatie, configuratiebronnen en prioriteit, JSON-schema, starten, testen, importformaat, security en beperkingen;
- voorbeeld-golden-set en mock/rooktest beschikbaar zijn;
- applicatie vanaf lege checkout met gedocumenteerde commando's start;
- geen API-key zichtbaar is in servergegenereerde HTML, API-responses, logs of exports; secretinvoer is uitsluitend zichtbaar tijdens invoer in lokaal GUI-passwordveld.

## 12. Beslisregister MVP v1.0

Onderstaande product- en implementatiebeslissingen zijn definitief voor MVP v1.0:

1. **Gebruiksvorm — BESLOTEN:** MVP is een lokale single-user webapp zonder accounts, rollen of centrale deployment.
2. **Endpointprotocol — BESLOTEN:** endpoints zijn OpenAI chat-completions-compatible. Temperatuur en reasoning horen bij het modeltestprofiel. Reasoning gebruikt een configureerbare veldnaam, standaardwaarden `off`, `low`, `medium`, `high` en een vrije custom waarde.
3. **Promptstructuur — BESLOTEN:** elk golden-set-item heeft één optionele system prompt en precies één user prompt. Multi-turn valt buiten MVP; architectuur blijft erop voorbereid.
4. **Judge — BESLOTEN:** één globaal standaard-judgeprofiel, optionele override per golden set en per run. Een item kan alleen rubric, threshold en context aanpassen, niet het judge-model. Standaardrubric beoordeelt feitelijke juistheid, volledigheid, relevantie, instructienaleving en consistentie, elk standaard met gelijk gewicht. Judge retourneert per criterium score en reden; applicatie berekent gewogen totaalscore. Scoreschaal is `0.0–1.0`, standaardthreshold `0.7`.
5. **Overige evaluatoren — BESLOTEN:** MVP bevat naast exact match en LLM judge ook contains, regex, numerieke vergelijking met tolerantie en JSON Schema-validatie. Semantische similarity valt buiten MVP.
6. **Endpointheaders en contract — BESLOTEN:** meerdere extra custom headers worden ondersteund en kunnen afzonderlijk als geheim worden gemarkeerd. API-keyprefix is configureerbaar, bijvoorbeeld leeg of `Bearer `. Requestbody- en responsepaden blijven vast volgens het OpenAI-contract, behalve expliciet configureerbare providerparameters zoals reasoning.
7. **Modellen ontdekken — BESLOTEN:** modellen kunnen handmatig worden toegevoegd of via een optionele configureerbare models-URL worden opgehaald. UI kan `/models` veilig voorstellen op basis van completions-URL. Gebruiker importeert geselecteerde modellen; bestaande profielen worden nooit automatisch overschreven.
8. **Kosten — BESLOTEN:** kosten zijn altijd in EUR. Per modelprofiel gelden standaard input-/outputprijzen, optionele cached-input- en reasoningtarieven, optionele vaste requestkosten en optioneel een instelbare contextdrempel `X` met afwijkende tarieven wanneer inputtokens groter zijn dan `X`. De volledige request gebruikt dan het hoge tarief. Token-subsets worden niet dubbel belast.
9. **Metrics en rangschikking — BESLOTEN:** primaire rangschikking is hoogste gewogen gemiddelde score, laagste foutpercentage en daarna laagste totale kosten. UI toont daarnaast pass rate, total latency, TTFT, TPS, input-/output-/cached-/reasoningtokens, target-/judge-/totale kosten en kosten per passed case. Targetcalls gebruiken streaming om TTFT en TPS te meten.
10. **Runschaal — BESLOTEN:** defaults zijn 10.000 items per golden set, 20 modeltestprofielen per run, 4 parallelle calls standaard en 10 parallelle calls maximaal. Alle grenzen zijn eenvoudig aanpasbaar via `.env`; MVP blijft single-user.
11. **Rate limits — BESLOTEN:** per endpoint zijn requests per minuut, tokens per minuut en maximale gelijktijdige requests configureerbaar. Leeg betekent onbeperkt door applicatie. HTTP 429 gebruikt `Retry-After` of exponentiële backoff met jitter.
12. **Data en privacy — BESLOTEN:** prompts en antwoorden kunnen gevoelig zijn. Ze worden onversleuteld in lokale SQLite opgeslagen en nooit in logs opgenomen. Logs gebruiken alleen run-ID, case-ID en uniek execution/test-ID. Retentie is via `RUN_RETENTION_DAYS` configureerbaar; leeg of `0` bewaart onbeperkt. Runs kunnen handmatig volledig worden verwijderd. Export waarschuwt voor gevoelige inhoud.
13. **Netwerk/security — BESLOTEN:** HTTPS is standaard verplicht. HTTP is toegestaan voor localhost/loopback en voor interne endpoints die expliciet als vertrouwd worden gemarkeerd. API-keys en geheime headers worden lokaal versleuteld opgeslagen; redirects naar andere hosts/protocollen worden niet automatisch gevolgd.
14. **Resultatenrapportage — BESLOTEN:** exports ondersteunen JSON, CSV en een zelfstandig offline HTML-rapport met print-CSS. Native PDF-generatie valt buiten MVP; gebruiker kan HTML via browser naar PDF printen.
15. **Restart-herstel — BESLOTEN:** duurzame SQLite-jobqueue hervat actieve runs automatisch. Afgeronde combinaties worden niet herhaald; onafgeronde of onderbroken jobs wel. Herhaling van een tijdens crash actieve providercall kan dubbele providerkosten veroorzaken en wordt zichtbaar gemarkeerd.
16. **Taal — BESLOTEN:** MVP-interface is uitsluitend Nederlands, zonder taalschakelaar. Code, API-velden, logs en technische documentatie zijn Engels. UI-teksten worden centraal beheerd voor latere internationalisatie.
17. **Deployment — BESLOTEN:** Docker Compose is primaire startmethode. Rechtstreeks starten via Python `.venv` op Windows en Linux blijft ondersteund.
18. **Quality gates — BESLOTEN:** optionele gates ondersteunen minimumscore, minimale pass rate, maximaal foutpercentage, gemiddelde/p95 total latency, p95 TTFT, gemiddelde TPS en maximale totale EUR-kosten. Gates zijn golden-setdefaults met runoverride. Uitkomst is `not_evaluated`, `passed`, `failed` of `indeterminate`.

## 13. Definitieve MVP-defaults

- Lokale, single-user Flask-webapp zonder login (**BESLOTEN**).
- Nederlandse UI zonder taalschakelaar; code, API, logs en technische documentatie in Engels; UI-teksten voorbereid op latere internationalisatie (**BESLOTEN**).
- OpenAI-compatible chat completions via volledige configureerbare URL; targetcalls gebruiken SSE-streaming, judge-calls blijven niet-streaming (**BESLOTEN**).
- Temperatuur en reasoning worden per modeltestprofiel ingesteld, zodat hetzelfde externe model met meerdere configuraties kan worden vergeleken (**BESLOTEN**).
- Reasoning-veldnaam is configureerbaar; ondersteunde standaardwaarden zijn `off`, `low`, `medium`, `high`, aangevuld met een vrije custom waarde (**BESLOTEN**).
- Endpoints, API-keyheader/prefix en extra custom headers zijn via GUI én configuratie-JSON beheerbaar; secretwaarden worden nooit geëxporteerd (**BESLOTEN**).
- Functionele configuratie gebruikt GUI + JSON. Operationele instellingen gebruiken GUI + `.env`, met prioriteit `.env > GUI/database > default` (**BESLOTEN**).
- Modellen worden handmatig toegevoegd of selectief geïmporteerd via een configureerbare OpenAI-compatible models-URL; bestaande profielen worden nooit overschreven (**BESLOTEN**).
- HTTPS is standaard verplicht; HTTP alleen lokaal of voor expliciet vertrouwde interne endpoints. OpenAI request-/responsepaden zijn vast, behalve expliciete providerparameters (**BESLOTEN**).
- Eén globaal standaard-judgeprofiel, optionele override per golden set en run; item kan alleen rubric, threshold en context aanpassen. Standaardrubric heeft vijf criteria, subscores en subredenen; totaalscore is gewogen en threshold is `0.7` (**BESLOTEN**).
- MVP-evaluatoren zijn exact match, contains, regex, numeric, JSON Schema en LLM judge; semantische similarity valt buiten MVP (**BESLOTEN**).
- Kosten zijn altijd in EUR. Modelprofielen ondersteunen input/output, cached input, reasoning, vaste requestkosten en optioneel een contextdrempel met hoge-contexttarieven; token-subsets worden niet dubbel belast (**BESLOTEN**).
- Duurzame SQLite-jobqueue met workerleases; geen externe queue. Actieve runs worden na restart hervat (**BESLOTEN**).
- Docker Compose is primaire deployment; Python `.venv` op Windows en Linux is ondersteund alternatief (**BESLOTEN**).
- Schaaldefaults: 10.000 items per set, 20 modelprofielen per run, concurrency standaard 4 en maximaal 10; alle grenzen zijn via `.env` aanpasbaar (**BESLOTEN**).
- Per endpoint zijn RPM, TPM en maximale concurrency optioneel configureerbaar; leeg betekent geen applicatielimiet (**BESLOTEN**).
- Prompts en antwoorden staan onversleuteld in lokale SQLite, nooit in logs; logs verwijzen via run-, case- en execution/test-ID's (**BESLOTEN**).
- Runretentie is configureerbaar; standaard onbeperkt. Handmatige verwijdering blijft mogelijk en export waarschuwt voor gevoelige inhoud (**BESLOTEN**).
- Golden-set-items bevatten één optionele system prompt en precies één user prompt; multi-turn valt buiten MVP (**BESLOTEN**).
- JSON en CSV voor golden-setimport/export; runresultaten daarnaast als zelfstandig HTML-rapport met print-CSS, zonder native PDF-generatie (**BESLOTEN**).
- Optionele quality gates beoordelen score, pass rate, errors, latency, TTFT, TPS en kosten; ontbrekende vereiste metrics geven `indeterminate` (**BESLOTEN**).
- Invoer- en responsegroottelimieten zijn via `.env` aanpasbaar met defaults van 1.000.000 prompttekens, 100.000 system-/judgecontexttekens, 1.000.000 verwachte-antwoordtekens, 10.000.000 modelantwoordtekens en 100 MiB voor import/providerresponse (**BESLOTEN**).
- Alle tijden in UTC, in UI weergegeven in lokale browsertijd.
