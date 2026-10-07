# grit-scan-platform

This repository is the current GRIT-Scan platform. The old private repository, `khengaikjony-ling_stengg/GRIT-Scan`, remains the archive. History starts here.

The SSH host is an AWS virtual machine. The model service it calls is GCP Vertex. Microsoft Dataverse remains the system of record. There is no scheduler, HTTP API, or installed package. You run a command from the repository root, or you start the Streamlit screen.

This page states what the code does now. Specs 13–33 still govern the rules. Spec 34 is the rebuild order. Parts of spec 34 are stale: the Dataverse gateway exists, and some live writes already happen. Those writes do not complete the workflow the spec describes.

## The rule

Agents propose. Deterministic functions decide. A named human approves.

A missing fact stays missing. The code does not fill a gap with a default, an inference, or a guessed value.

A missed market is an `OPPORTUNITY_COST` signal. It is not a harm score.

No scoring path calls a language model. No likelihood or probability estimate is part of signal scoring. Spec 16 is an annual-assessment instrument. Do not import it into a signal score.

## Current shape

The repository holds five paths. The first four share types and a Dataverse gateway, and they do not form one pipeline. The fifth, the digest pipeline, runs on files under `output/`.

| Path | What it decides | What it writes | What it does not do |
|---|---|---|---|
| Harm scorer, `grit_scan/impact_scoring.py` | Evidence, horizon, severity, transmission, high-negative-impact verdict, opportunity cost | Nothing. It is a pure function. | It is not called by the weekly cascade, the wild scan, the Dataverse import, or the agents. |
| Weekly cascade, `grit_scan/weekly_pipeline.py` | Condition match, exposure and theme binding, communication priority, Trend, Risk Level, Basis of Relevance | Local `ranking.json` and a text delta. A separate command can copy the ranking into Dataverse. | It does not scrape, and it does not call the harm scorer. |
| Wild scan, `dataverse/run_wild_scan.py` | Whether a headline can be stored, then a best-effort URS-TW theme label | Currents headlines, as `NEW` signals. GDELT export hits are printed and not stored. | It does not score harm, fetch an article body, or advance past `NEW`. |
| Review shell, `supervisor/` and `ui/` | `TransitionGuard()` and `ReviewGate()` | An in-memory ledger. The screen's approve action does not write Dataverse. | The live import paths do not run this chain to `APPROVED`. |
| Digest pipeline, `acquisition/brave_plan.py` to `report/run.py` | The link to the ontology, event type, briefing priority, and the six-question finding cards | Files under `output/brave-plan/DATE/`. | It does not write Dataverse, call the harm scorer, or approve anything. |

If you change how the platform works, choose which of these paths is the product. Today they answer different questions and store different fields under the same Dataverse columns.

## Two label systems

Do not treat these names as the same judgement.

The harm scorer returns a reconstructable assessment:

| Result | Values | Rule |
|---|---|---|
| Evidence | `HIGH`, `MEDIUM`, `LOW` | Outlet count, tier, and an SOP caveat. Syndication is not yet separated. |
| Horizon | `MATERIALISED`, `IMMINENT`, `DEVELOPING`, `LATENT` | Taken from `expected_impact_date`. Absent date stays absent. |
| Severity | `INSIGNIFICANT` through `SEVERE`, or `NOT_ASSESSED` | Maximum across Financial, Health & Safety, Regulatory Compliance, and Reputation. Never an average. |
| Financial severity | A band, or `INSUFFICIENT_DATA` | Value-at-risk only. Contract value is not used. The workbook has no value-at-risk, so real signals return `INSUFFICIENT_DATA`. |
| Transmission | `DIRECT`, `COUNTERPARTY`, `PERIMETER`, `SECTOR`, `AMBIENT` | Bound from the transmission registry. `AMBIENT` has no factor. |
| Verdict | `HIGH_NEGATIVE_IMPACT`, `POTENTIAL_HIGH`, `NOT_HIGH` | A function of severity, transmission, and evidence. Not a likelihood. |
| Opportunity cost | `NOT_ASSESSED`, `NOT_APPLICABLE`, `NOT_LINKED`, `PRESENT`, `OPPORTUNITY_COST` | Prose is never read as a market opening. A human must confirm `OPPORTUNITY_COST`. |

`assess_impact()` orchestrates those functions. It makes no judgement of its own. Spec 34 names this `ScoreRisk()`. Do not build a second harm scorer.

The weekly cascade and the wild scan write a different set of labels into Power Apps columns on `grit_signals`:

| Column | Weekly ranked finding | Wild-scan Currents row |
|---|---|---|
| `grit_headline` | Condition name. Power Apps shows this as Theme. | Matched URS-TW condition name, or `Unclassified wild-scan signal (no governed URS-TW condition matched)`. Never the article title. The title is `grit_signalname`. |
| `grit_trend` | The development's movement: `ESCALATING`, `EASING`, or `NO_MATERIAL_CHANGE`. | Always `NO_MATERIAL_CHANGE`. One article is not evidence of movement. |
| `grit_risklevel` | `HIGH`, `MEDIUM`, or `LOW`, copied from communication priority. `WATCHPOINT` becomes `LOW`. | `HIGH`, `MEDIUM`, or `LOW` from the matched STE theme tier (`KEY`, `TIER_2`, `EMERGING`). No match is `LOW`. |
| `grit_businessrelevancebasis` | Built from domain, matched themes, bound entities, and an evidenced descent. | The matched condition's governed `taiwanTransmission` sentence, plus a single-source unreviewed caveat. No match says so. |
| `grit_evidenceconfidence` | The confidence the scan JSON already stated. | Always `LOW`. |

These columns are briefing labels. They are not a GRIT severity, a likelihood, or an approval.

`grit_scan/relevance_scoring.py` builds the weekly labels from ontology fields, not from a table of Taiwan sentences. `grit_scan/weekly_pipeline.py` still adds `+2` when the matched theme list contains the literal string `Taiwan business concentration`. That bonus is not generalized.

## Weekly finding path

The weekly cascade expects scan text that already contains a JSON array. Each object needs `conditionHint`, `movement`, `evidenceConfidence`, `summary`, and `sources`. It can also carry `descentCandidate`. `grit_scan/docx_reader.py` can read a `.docx`. The cascade does not call it.

`import_scan()` accepts a development only when all of the following hold:

- `conditionHint` matches exactly one baseline condition, by id, name, normalised name, unique domain, or a unique partial name.
- `movement` maps to `ESCALATING`, `EASING`, or `NO_MATERIAL_CHANGE`.
- `evidenceConfidence` maps to `HIGH`, `MEDIUM`, or `LOW`.
- The summary is non-empty.
- A reported change has at least one non-placeholder source URL with a path.

`revise()` binds exposure entities and STE themes. A theme keyword hit is `PER_FINDING_KEYWORD_MATCH`. No hit falls back to the condition's own `steThemes`, marked `CONDITION_DEFAULT_FALLBACK`. A descent is `SUPPORTED` only when the scan supplied a complete descent, evidence confidence is `HIGH`, and `lob_match()` hits the pack. Otherwise the descent is `NOT_JUSTIFIED` or `INSUFFICIENT_EVIDENCE`. The cascade does not invent the descent.

`rank()` drops `NO_MATERIAL_CHANGE` items. It scores the rest for briefing order:

- high evidence `+2`, medium evidence `+1`;
- escalating `+1`, easing `-1`;
- the Taiwan business-concentration theme `+2`;
- extra STE themes, capped at `+2`;
- a supported descent `+2`.

`HIGH` is 5 or more. `MEDIUM` is 3 or 4. Anything lower is `WATCHPOINT`. Credibility lookup is attached here. It does not change the confidence the scan already stated. `compose_delta()` writes the human-readable review note.

`run_weekly_pipeline()` writes `revision-input.json`, `revision.json`, `ranking.json`, and `weekly-delta.txt` under the output directory you pass. There is no command-line wrapper for that function.

`python3 -m grit_scan.run_weekly_ontology` is not this path. It is a shadow comparison. It reads `output/2026-09-21/`, assigns outlet tiers from a hard-coded table, and prints `assess_impact()` beside the document's own movement labels. Those tiers are not a governed credibility policy.

## Wild-scan path

`acquisition/` emits candidates. It must not import `assess_impact`. Lanes are `APPROVED`, `WILD`, `SPHERA`, `AGILE`, and `ISOS`. Sphera and AGILE scores are dropped on the way in. `ISOS` returns an empty list until a feed exists. Only GDELT and Currents have live collectors. The other lanes adapt records you already hold.

The GDELT scan reads the public 15-minute events export: one download from `lastupdate.txt`, then a local filter per query. It does not poll the DOC API. `--timespan` and `--pause-seconds` are accepted so older commands still parse. They are unused. Tone and Goldstein scores are not read. `DATEADDED` is `seen_at`, not `published_at`. The export has a URL and no headline, so those rows are an index. The command prints them and leaves Dataverse unchanged. A failed download or a bad checksum raises `GdeltFetchError`. That is not an empty week.

Currents is `currentsapi.services`, not GDELT. One run allows one or two queries. The command caps a pull at 20 records. Only a row with a title and a URL can be written. A failed pull raises `CurrentsFetchError`.

A written Currents row is a `NEW` signal. Theme matching uses the candidate title only, against `config/urs-tw/URS-TW-STE-Annual-Risks.json`, then the URS-TW baseline condition with the most overlapping theme labels. The comparison is a normalised word-subset match, because a condition's `steThemes` labels are short paraphrases. No keyword hit stays unclassified. That is intentional. The match is hard-coded to the Taiwan pack. It does not follow `--pack`, and the `urs` pack has no conditions to match.

`dataverse/backfill_wild_scan_relevance.py` refreshes Theme, Trend, Risk Level, and Basis of Relevance on rows whose correlation id starts with `GDELT-` or `CURRENTS-`. The current wild-scan command does not create `GDELT-` rows. The backfill does not touch a weekly ranked finding, an approval field, or workflow state.

## Harm scoring

`grit_scan/impact_scoring.py` is stdlib-only. `SCORING_MODULE_VERSION` is `0.5.0`. `SeverityBand` is an `IntEnum`, and `INSIGNIFICANT == 0`. Compare a band explicitly. Do not truth-test it.

Absence uses one sentinel per cause. `ValueAtRiskBasis.INSUFFICIENT_DATA` is for the Financial dimension only. `DimensionAssessment.NOT_ASSESSED` is for the other dimensions, and for the overall band when every dimension is absent.

The transmission registry comes from the workbook (see Provide the workbook) plus `config/registry_aliases.json`, through `grit_scan/registry_loader.py`. The severity definition is parsed from `specs/15_Severity_Definition.md`. `grit_scan/inspect_severity.py` writes that parse as JSON. `grit_scan/backtest_shadow_mode.py` runs the scorer against an audited-signals export and prints a comparison. It does not write the workbook, and it does not treat the human audit as ground truth. The audited export has no structured observations, no value-at-risk, and no expected impact date, so those fields stay absent.

`grit_scan/risk_taxonomy.py` resolves a taxonomy reference. A taxonomy tag is not an arithmetic multiplicand.

## Workflow and review

`TransitionGuard()` and `ReviewGate()` are pure. They do not read or write state. The Supervisor is the only writer of the in-memory `GRIT_WorkflowState` ledger. A denied decision appends nothing. The initial `NEW` row is the spec 20 exception and has no guard.

The in-memory graph is:

`NEW` → `FETCHED` → `DEDUPED` → `ASSESSED` → `CRITIQUED` → `PENDING_REVIEW` → `APPROVED` → `PUBLICATION_CONFIRMED` or `AIRIS_HANDOFF_READY`.

`PENDING_REVIEW` can return to `ASSESSED` as rework. Rework is package-wide: every member signal returns together. `FAILED` can be restored. `PUBLICATION_CONFIRMED` and `AIRIS_HANDOFF_READY` are terminal.

`ReviewGate()` returns `APPROVE`, `REJECT`, `HOLD`, or `RETURN_FOR_REWORK`. An approval names the package id, version, and content hash. The contract tests prove `NEW` → `APPROVED` in memory, including stale-sequence denial, an idempotency conflict, and `FAILED` → `RESTORE`. That chain is not wired to Dataverse.

The Streamlit screen, `ui/app.py`, calls `ReviewGate()` through `ui/actions.py`. It does not write the ledger itself. Its approve, reject, hold, and rework buttons act on a seeded sample package. `SIG-1` is that sample. It is not a Currents row. The top of the screen lists acquired Currents signals from `output/currents-latest.json` and, when Dataverse credentials are present, the latest `CURRENTS-` rows. That list is display only. `NOT_ASSESSED` and `INSUFFICIENT_DATA` render as not established. Empty fields are omitted.

## Agents

Four agents propose: Researcher, Assessment, Critic, and Reporting. Prompts are versioned in `agents/prompts.py`. A response must not contain a score, severity, likelihood, credibility rating, route, or aperture. The Critic must report `NOT_VERIFIABLE` rather than fill a gap. Researcher references are limited to URLs the search tool returned.

`agents/tools.py` checks entities, URLs, and corroboration against data you supply. It does not browse. The Supervisor rejects a proposal that cites an entity outside `known_entity_ids`, and it does not append a workflow row for that rejection.

`python3 -m agents.run_pass` loads the latest `CURRENTS-` signal, runs the four agents through Vertex, and writes `grit_executions` plus `grit_agentexecutionlogs`. It does not write a score and does not advance workflow. `grit_logstatus` value `SUCCEEDED` means the call finished. It does not mean the proposal was accepted.

Gemini uses Vertex. A service-account file is used when `GOOGLE_CLOUD_PROJECT` and `GOOGLE_APPLICATION_CREDENTIALS` are both set. Otherwise the client sends `GOOGLE_API_KEY` or `GEMINI_API_KEY` in Vertex express mode. `.env.example` and the missing-credential message say an API key is rejected. The code path still sends a key when no service account is configured. A value that looks like a private key or a JSON file is refused. The known pasted service-account key id is refused before any call. No Groq or Llama proposer remains.

## Theme and event layer

`grit_scan/theme_layer.py` calculates theme metrics, matches events and themes, assesses escalation, and gates a register mutation. These functions do not write. `ThemeEscalationAssessment()` does not return `CLOSE_CANDIDATE` for a `STANDING_RISK`. Closing a standing risk requires `ANNUAL_REPORT_GOVERNANCE`, not a metric result. Standing-risk names are not hard-coded. The Annual Report register is not in this repository.

`supervisor/theme_register.py` appends a theme or event version only after `RegisterWriteGate()` returns `ALLOW`. A theme write does not append a workflow row. The register is in memory. Dataverse does not receive it. The weekly cascade and the wild scan do not call this layer. `horizon_pack_sections()` returns sections. It does not render a document.

## Reader packs

`grit_scan/ontology.py` loads one pack. Taiwan is a reader, not the whole ontology.

| Pack | Role | Baseline | How it is produced |
|---|---|---|---|
| `urs` | Urban Solutions. This is the default for weekly import. | `URS-LAYER1-ABSENT`. The condition list is empty. No Taiwan condition is copied. | `python3 -m grit_scan.build_urs_pack`, from the workbook. |
| `urs-tw` | Taiwan reader pack. | Ten governed baseline conditions, including their `taiwanTransmission` sentences. | Copied from the archived production registers. Not rebuilt by `build_urs_pack`. |

An empty `urs` baseline means `import_scan()` cannot accept a development for that pack: every `conditionHint` is unmatched. Weekly import still defaults to `urs`. Pass `--pack urs-tw` only when the ranking was produced from the Taiwan baseline.

The `urs` source register lists 130 sources from three workbook sheets: `News-Pri` (regulators and other first-hand sources, with their section headings), `News-Sec` (wire services and news outlets), and `9. Specific Industry News`. It does not rate them. An unmatched outlet stays `UNRATED`. A named row with no URL goes to `skippedWithoutUrl` and gets no invented domain. The workbook has no project sheet and no site register. Those lists stay empty. Country of incorporation is not treated as a priority country.

The `urs` exposure register has 41 entities. Each entity keeps the workbook's own `EntityId` as `sourceEntityId`. The workbook names LOBs differently on different sheets. Sheet 2 uses `Mobility (Rail & Road)` and `Robotics`. Sheet 6 uses `Mobility Rail`, `Mobility Road`, and `Heatlhcare Robotics & Automation`. The `URS` sheet uses capability names. The org chart on `1. Org Chart` is an image that names the LOBs, so `config/urs/URS-LOB-Hierarchy.json` records its business area, LOB groupings, and LOB names by hand. The file records no individual. Its status is `TRANSCRIBED_FROM_IMAGE_PENDING_BA_VALIDATION`. The builder uses it to give each LOB profile a `canonicalLob`, `baName`, and `lobGrouping`. A label that spans several LOBs, such as `Mobility (Rail & Road)`, or that names a capability, such as `Robotics`, is listed under `unresolvedLabels` and is never assigned. The builder reads the hierarchy as an input and does not overwrite it.

The `urs` pack also carries three registers built from workbook sheets that feed search:

- `URS-Keyword-Library.json` holds the seven hidden LOB keyword sheets. Each LOB has tier-1 terms, numbered clusters with the priority the sheet states, a geopolitical watchlist, tag clusters, a top-keywords list, and emerging risk themes where the sheet numbers them. The register records that the sheets are hidden and not validated. It copies no note text.
- `URS-Competitors.json` holds 201 competitor rows by LOB label. A name with no workscope that heads a block of named competitors, such as `Traffic Management`, is a product line. It goes to `dropped` as `SUBHEADING`, and the rows beneath it carry it as `category`.
- `URS-Management-Focus.json` holds four numbered management requests and ten focus items. Status text is copied unchanged, and a status that says research draft means no business area has validated the item.

- `URS-Relationships.json` is hand-authored and starts with no rows. It holds relationships and aliases the workbook lacks. See [Ontology graph and link](#ontology-graph-and-link).

A row that a parser cannot place goes to a `dropped` list with a reason. To print the counts, run `.venv/bin/python -m grit_scan.ontology --pack urs`.

Matchers in `grit_scan/matchers/` bind text. They do not score. A keyword shorter than 6 characters matches only as a standalone word, so `ai` does not match inside `Taiwan` and `war` does not match inside `warehouses`.

## Dataverse writes

`dataverse/client.py` is the only module that calls Dataverse. Auth is app-only, through `msal`. Credentials come from the environment. A text `$filter` has missed an existing row before, so content-hash uniqueness is resolved in Python. A repeated hash is patched. Approval fields are not part of that patch. Timestamps sent to Dataverse must be ISO 8601, such as `2026-09-24T02:15:47Z`.

Live writes today:

| Command | Tables | Workflow effect |
|---|---|---|
| `python3 -m dataverse.run_wild_scan --currents` | `grit_executions`, `grit_signals`, `grit_workflowstates` | Creates `NEW` only. Does not call `TransitionGuard()` for that row. |
| `python3 -m dataverse.run_weekly_import` | Same three tables | Creates `NEW` for a new finding. A repeat refreshes Trend, Risk Level, Basis, and evidence confidence, and does not add a second workflow row. |
| `python3 -m agents.run_pass` | `grit_executions`, `grit_agentexecutionlogs` | No workflow advance. |
| `python3 -m dataverse.backfill_wild_scan_relevance` | `grit_signals` display columns only | No workflow change. |

Choice codes live in `dataverse/choices.py`. Do not invent new ones. The code does not write a severity, a likelihood, a publication date on a wild-scan row, or an article body.

The in-memory Supervisor also records a `NEW` row during those imports. That ledger dies when the process exits. Dataverse is the copy that remains, and it holds only the `NEW` row the gateway inserted.

## Query plan

`grit_scan/query_plan.py` turns the `urs` ontology into search queries. It makes no network call and calls no model.

The plan has 12 lanes, in this order of preference: tag clusters, emerging themes, scan keywords, tier-1 terms, sheet 6 keywords, STE theme keywords, management focus, suppliers, customers, competitors, entities, and the remaining cluster terms. Each query keeps the register rows it came from. A query that appears in more than one lane keeps its first lane, merges the sources, and takes the shorter cadence. Supplier, customer, competitor, and entity names are marked `exact`, so a search adapter can quote them. A supplier, customer, or entity is searched by the name news uses: its reviewed alias, or its register name without the legal suffix and trailing country (`MOTOROLA SOLUTIONS SINGAPORE PTE. LTD.` becomes `MOTOROLA SOLUTIONS`). A legal name sent as an exact phrase finds almost nothing. The source record keeps the register name and the basis. No other term is reworded. The market request on the management focus sheet adds one query per country and per sector that its scope names, such as `Thailand Rail sector`.

Each query has a cadence in weeks. Critical tag clusters, management focus, suppliers, and customers run every week. Scan keywords and High tag clusters run every 2 weeks. Entities run every 2 weeks, competitors every 8, and the bulk cluster terms every 12. Everything else runs every 4 weeks. A query is due on a fixed slot inside its cadence, derived from its id. The whole plan is therefore covered once every 12 weeks, and the selection for a date is the same every time you compute it. A weekly run takes the due queries lane by lane, up to a cap. The cap defaults to 350, so five weekly runs stay inside the 2,000 queries a month of the free Brave plan. The selection record lists the queries the cap deferred.

To print a week's plan, run the following command. It writes nothing unless you pass `--json`.

```bash
.venv/bin/python -m grit_scan.query_plan --date 2026-10-12 --max-queries 350
```

## Brave search run

Two modules collect candidates from the Brave News Search API. Neither scores a result or fetches a page.

- `acquisition/brave.py` is the small pull: one Boolean query per active LOB, at most six per run. It prints candidates and stores nothing.
- `acquisition/brave_plan.py` sends the query plan and stores the run.

A plan run writes to `output/brave-plan/DATE/`, which Git ignores:

- `selection.json` holds the selected queries and the ones the cap deferred.
- `searches.jsonl` holds one line per query, written as each search returns. A failure part-way through loses nothing that already returned.
- `candidates.json` holds one candidate per page, plus every query that found it and its rank.

A page that several queries find is one candidate. Its URL loses tracking parameters and a trailing slash. Names are sent as exact phrases, and topics are not. A candidate's text is the engine's description plus its extra snippets. Its `published_at` is Brave's `page_age`, read as UTC. The command refuses to run a date twice, because each run spends queries. Delete the date's folder to run it again.

A run stops early, and says why in `candidates.json`, when it reaches `BRAVE_MONTHLY_LIMIT` (default 2000, counted locally) or when Brave answers HTTP 429 after two retries. Any other error status stops the run with an error. The pace follows the key's per-second rate-limit header.

> **Warning:** The key's rate-limit headers report 50 requests per second and no monthly figure. Only the local counter in `output/brave-usage.json` enforces the monthly limit, and it ignores queries sent outside this command. If your plan bills per query beyond a free allowance, a full run can cost money. Check the plan in the Brave dashboard before you run the full plan.

`--limit-queries N` sends N queries dealt one per lane in turn, so a trial tries every lane. Each lane keeps its own order, which puts its highest priority first. An earlier version took the first N, which were all tag clusters, so a 20-query trial never searched a supplier, customer, competitor, or entity name.

To print a trial without sending anything, and then to send 20 queries:

```bash
.venv/bin/python -m acquisition.brave_plan --date 2026-10-12 --dry-run --limit-queries 20
.venv/bin/python -m acquisition.brave_plan --date 2026-10-12 --limit-queries 20
```

`--country` defaults to `ALL`. Brave rejects a code such as `SG` with HTTP 422.

## Semantic retrieval

`retrieval/` compares text by meaning. It does not score harm, decide an STE link, or write to Dataverse. Dataverse stays the system of record, because it has no vector search.

- `retrieval/embedder.py` calls Gemini (`gemini-embedding-001`, 768 dimensions) through the same credential checks as the agents. It normalizes each vector to unit length and rounds it to float32.
- `retrieval/store.py` caches vectors in `output/vector/index.sqlite`, keyed by model, task, dimensions, and text hash. An unchanged text is never embedded twice. Delete the file and it refills.
- `retrieval/similarity.py` computes cosine similarity in plain Python, so the same vectors give the same numbers on any machine.
- `retrieval/anchors.py` builds 175 anchors from the registers: tag clusters, emerging themes, LOB scopes, STE themes, management focus items, and market-request topics. An anchor is a topic, never a name. The deterministic bind matches names.

To embed a Brave run and score it against the anchors, run the following command. It writes `anchors.json` and `semantic.json` into the run folder, with the three best anchors and their cosine scores for each candidate. It drops no candidate and applies no threshold. Use `--dry-run` to count the texts without calling Vertex.

```bash
.venv/bin/python -m retrieval.embed_run --run output/brave-plan/2026-10-06
```

> **Note:** Scores sit in a narrow band. On the 2026-10-06 trial of 360 candidates they ran from 0.54 to 0.74, with a median of 0.66. A story about airport trams scored 0.644 for a platform-screen-door query, so one global threshold does not separate relevant from irrelevant candidates.

## Ontology graph and link

`grit_scan/ontology_graph.py` builds one typed graph from the pack's registers, and `grit_scan/link.py` places a candidate on it. Both are pure: no network, no model, no score.

**Graph.** Classes follow `schema.yaml`: `STE`, `LOB`, `Customer`, `Supplier`, `Org` for a competitor, `Place`, `Site`, and `ManagementRequest`. `Theme` is an addition. Relations are `operates_in`, `located_in`, `supplies`, and `focuses_on` from `schema.yaml`, plus three additions: `part_of`, `buys_from`, and `competes_in`. On the `urs` pack the graph has 324 nodes and 319 edges. Every edge carries a status and a basis:

| Status | Meaning |
|---|---|
| `SOURCE_STATED` | The workbook says it. The basis names the sheet. |
| `DERIVED` | A named rule computed it from stated facts. |
| `PROPOSED` | A hand-authored row in `URS-Relationships.json`. Not validated. |
| `VALIDATED` | A hand-authored row with `approvedBy` and `approvedOn`. |

The graph never invents an edge. What the workbook lacks is listed in `graph.gaps`:

- 30 of 41 entities have no single LOB. 28 carry `Mobility (Rail & Road)` and 2 carry `Robotics`. They link to their LOB grouping only.
- No customer links to an entity or a LOB. A customer reaches the business area.
- A supplier links to a unit label, `URS Singapore` or `URS Taiwan`, and not to an entity.
- 64 competitor rows carry a LOB label that matches no org chart LOB.

`reach(node)` walks from any node to the STE nodes it connects to, up to three hops, through LOBs and places only. It never passes through a customer, a competitor, or a management request, and it never goes up a hierarchy and back down into a sibling LOB. A Singapore customer story therefore reaches the business area, and a Mobility Rail story reaches the Smart Mobility entities and not the Mobility Road ones.

**Names.** A node is found by its register name, a reviewed alias from `registry_aliases.json`, an alias from the overlay, or a generated short name: no bracket, no legal suffix, no trailing country. A generated name is refused when it is one generic word such as `Group`. An acronym of three or four capitals matches in its own case only. The longest name wins where two overlap. `ST Engineering` belongs to the group node, not to an entity. Each match records its basis: `REGISTER`, `REVIEWED`, `OVERLAY` for an alias the BA accepted, `DERIVED` for the group name, or `GENERATED`.

> **Note:** A generated name is the register name without its suffix, so it cannot tell two companies with one name apart. `Amtech Systems` also names a US-listed semiconductor company, and `Ministry of Home Affairs` names a ministry in every country that has one. The alias review below is the control.

**Link.** A candidate lands on one rung, strongest first:

| Rung | The text |
|---|---|
| `DIRECT` | names an STE entity, the group, a project, or a site. |
| `COUNTERPARTY` | names a registered customer or supplier. |
| `COMPETITOR` | names a registered competitor. |
| `PERIMETER` | matches a LOB keyword and names a country where an entity of that LOB's grouping is registered. |
| `SECTOR` | matches a LOB keyword and nothing else. |
| `THEME` | names nothing from the register and is a big event. The filter sets this rung. See [Briefing priority](#briefing-priority). |

A name counts only when it is strong: in the title, in the first 400 characters, or named twice. A single mention in the body is recorded as a passing mention and does not raise the rung. A one-word name that is not an acronym, such as `Leonardo` or `Alstom`, also needs a LOB keyword in the same text. Without that rule, `Leonardo DiCaprio` linked to the supplier `Leonardo`. A country alone is never a link, and neither is a theme. The LOB rule is unchanged: exactly one active LOB must match. A link records its matched text, where it matched, the alias basis, the themes it hits, and a reason per decision.

**Document type.** `grit_scan/doc_type.py` types each candidate before it ranks. These report no event, and the filter drops them as `NOT_AN_EVENT`:

| Type | Example |
|---|---|
| `MARKET_RESEARCH` | `Smart Water Meters Market worth $10 billion by 2031` |
| `LISTICLE` | `Top 10: Smart Grid Providers` |
| `FINANCE_COMMENTARY` | `Amtech Gains 79% in the Past Year: Should It Be in Your Portfolio?` |
| `EXPLAINER` | `Competitor analysis: how to perform one in 5 easy steps`, a sponsored post, or an opinion piece |

A wire release is typed `PRESS_RELEASE`, and everything else is `NEWS`. In the 2026-10-06 sample, 160 of 1,223 candidates were not events.

## Briefing priority

`grit_scan/scale_cues.py` and `grit_scan/priority.py` decide what to read first. They are pure: no model, no score, and no severity or likelihood. Every component is logged on the decision.

A **big event** hits a KEY STE theme in its title or lead, and meets at least two of three cues:

| Cue | Rule |
|---|---|
| Outlets | Three or more outlets carry the event. Pages from different outlets at cosine 0.90 or above are one event. A wire service counts once. |
| Action | A regulator or government action, a war, or a pandemic, in the title or lead. An agency acronym such as `DOE` matches in capitals only. A word that also opened the theme gate does not count again: `tariff` is a KEY-02 keyword and an action cue, so it counts once. |
| Figure | A stated amount of at least 1 million in USD, SGD, EUR, GBP, AUD, CAD, or CHF, read as stated. No exchange rate is applied. An amount in another currency, below a million, or beside a market projection is listed and not counted. |

The KEY theme is required. The bar of two cues is `--min-cues` on `filter_run`. With one cue, a campaign launch and a distribution deal passed. In the 2026-10-06 sample, no event met two cues, and 10 met one.

A **direct** signal is on the `DIRECT` or `COUNTERPARTY` rung. It is **significant** when it hits a theme in its title or lead, or meets a cue. A name alone is not significant.

| Tier | Signal |
|---|---|
| 1 | Big and direct. |
| 2 | Big, or direct and significant. |
| 3 | Linked, and neither. |
| 4 | Not linked. |

A big event with no register link is promoted to the `THEME` rung, because the KEY theme is the link the register states for it. Inside a tier the order is the best theme tier hit (`KEY`, then `TIER_2`, then `EMERGING`), the cues met, the rung, and the outlets. The themes are how the register states what matters to STE.

## Hand the gaps to the BA

The graph cannot say which entity a customer buys from, and it cannot tell two companies with one name apart. The BA can. Export the questions, then import the answers:

```bash
.venv/bin/python -m grit_scan.relationship_gaps export
.venv/bin/python -m grit_scan.relationship_gaps import --relationships output/ba-review/relationships-to-fill.csv --aliases output/ba-review/aliases-to-review.csv --dry-run
```

Export writes two CSV files to `output/ba-review/`. They hold names from the confidential workbook, so do not commit them.

- `relationships-to-fill.csv` has 129 rows on the 2026-10-06 workbook: 30 entities with no single LOB, 20 customers, 15 suppliers, and 64 competitors whose LOB label matches no org chart LOB. Each row lists the candidate node ids. The BA writes the answer node ids in `answer_node_ids`, separated by `|`, and a `basis`. A row with `approved_by` and `approved_on` imports as `VALIDATED`. Any other answered row imports as `PROPOSED`.
- `aliases-to-review.csv` has 150 rows: every name the graph searches for that the register does not state, with a risk hint such as `ONE_WORD_NAME`. The BA writes `ACCEPT` or `REJECT` in `decision`. A rejected alias is switched off.

Import checks every row. One bad row stops it before anything is written. It merges the answers into `config/urs/URS-Relationships.json`, and a second export leaves out what that file already answers. The merge follows these rules:

- An accepted alias becomes a firm `OVERLAY` alias, and it leaves the review list.
- The later decision on an alias wins. A rejection removes every spelling of the name, so rejecting `LEONARDO` also removes `Leonardo`.
- A `PROPOSED` row never replaces a `VALIDATED` one for the same relationship, and the import says how many it ignored.
- Import checks the rows against the graph the workbook alone gives, so a BA can reverse an earlier decision.

## Signal filter

`retrieval/filter_run.py` applies the keep rule to an embedded Brave run. It reads local files and the vector cache and calls no API. Every candidate gets one decision in `decisions.jsonl`, and the kept signals go to `signals.json`. The rule runs in this order:

1. **Stale.** A page older than 8 days is dropped. A page with no date is kept.
2. **Not an event.** A market-research listing, a ranked list, an investment note, or a how-to or opinion piece is dropped.
3. **Link.** `grit_scan/link.py` places the candidate on a rung, or on none. A decision records the rung, the link, and the document type. It also keeps the fields the first report reads: `link_level`, `entity_names`, `match_types`, `canonical_lob`, and `evidence_spans`.
4. **Candidates to keep.** A linked page stays. A page with a strong KEY theme hit stays for the next steps. Any other unlinked page stays only if it ranks in the top 5, by similarity to the query text, among the results of a query that found it.
5. **Duplicates and one event.** Pages at cosine 0.95 or above are one story. A linked page, or one with a strong KEY theme hit, also absorbs pages from other outlets at cosine 0.90 or above, taken in rung order, so one event is one finding and the page with the strongest link stays. The merged outlets count toward the outlets cue and show as "also carried by". Outlets are counted with every wire service as one.
6. **Priority.** Each survivor gets a tier and its components. A big event with no register link is promoted to the `THEME` rung. An unlinked page that is not big must still rank in the top 5 of a query.
7. **Order and caps.** The order is tier, best theme tier, cues met, rung, outlets, topic similarity, a registered source, recency, and URL. Linked signals are capped at 40 and unlinked signals at 10, with one slot per event: a page at cosine 0.90 or above to a kept page is cut. Every cut is logged.

The drop reasons are `STALE`, `NOT_AN_EVENT`, `NOT_TOP_IN_QUERY`, `NEAR_DUPLICATE`, `SAME_EVENT`, `SIMILAR_TO_SELECTED`, `BOUND_OVER_CAP`, and `UNBOUND_OVER_CAP`.

```bash
.venv/bin/python -m retrieval.filter_run --run output/brave-plan/2026-10-06
```

> **Note:** The 2026-10-06 sample is 84 queries dealt across the 12 lanes. It returned 1,223 candidates. The filter kept 40 linked signals (13 in tier 2, 27 in tier 3) and 10 unlinked. Without the name lanes, the earlier 20-query trial found no direct link.

## Finding loop

`loop/run.py` writes a six-question card for the top signals of a filtered run. The model drafts, deterministic code checks the draft against the ontology, the model reviews its own draft against the same ontology, a tool adds evidence when it is thin, and the model refines. The model proposes. Deterministic functions decide what is valid and compute the confidence.

For each finding, the loop runs these stages. `--top` (default 10) and `--max-tier` (default 2) choose the findings:

1. **Context.** `loop/context.py` builds what the model may use, and it carries no URL: the candidate STE entities, the ontology paths with ids (`P1`), pages that carry the same event from other outlets (`E1`), the register facts on the paths (`R1`), the themes hit, and what the register lacks. A candidate entity is selectable only when the text names it, or names its country and the story's LOB.
2. **Draft.** The model returns the card as JSON. It starts with an identity check, described below.
3. **Check.** `loop/card.py` tests every claim against the context. A quote must be at least 20 characters and verbatim in its source. An identity quote needs 4. Entity, path, evidence, and theme ids must exist. A number must appear in the text as a whole number: 5 is not in 50, and 20 is not in 2026. A value of the wrong type is a violation and never an error. These checks decide. A violation goes back to the model.
4. **Evaluate.** When the checks pass, the model reviews its own draft against the context and lists issues by question. These are advice for the next draft, never a decision.
5. **Tool.** When fewer than 2 other outlets carry the event, one follow-up Brave search runs. The model proposes the query, and the code accepts it only if it is short, plain, and about the signal. Otherwise the headline is the query. Each finding may use one search, and the run may use 10 (`--follow-ups`). Each search counts against the monthly guard and is logged in `followups.jsonl` with the signal it was for. A repeat for the same signal in the same run folder reads the log and spends nothing, whatever wording the model chooses. `--no-search` turns the tool off. A search or embedding error is recorded and does not stop the loop.
6. **Refine.** The model revises the card with the violations, the issues, and any new evidence.

The loop stops on `CHECKS_PASSED`, `NO_CHANGE`, `NO_PROGRESS` (the model raises the same issues again), `MAX_ITERATIONS` (default 3), or `LLM_ERROR`. A part that still fails its checks is withheld and the card says so. A refine that fails keeps the last card that passed its checks. An unexpected failure withholds that one card, with the reason, and the next finding still runs.

**The six questions.**

| # | Question | Who answers | What the code checks |
|---|---|---|---|
| 1 | What happened? | The model, from the signal text. | Every quote and every number is in the text. No URL and no banned word. STE is not named unless the text names it. |
| 2 | Which STE entity does it affect? | The model picks from the selectable candidates. The code states the level the register supports: entity, LOB, unit, business area, or none. | The id exists, and the text supports it. |
| 3 | Which ontology relationships connect it to other entities? | The model cites paths. The code prints each path with its status and the workbook sheet. | Every path id exists, and a path is cited when one exists. |
| 4 | What independent evidence supports the connection? | The model cites evidence and quotes it. The code decides independence: another outlet, wire services counted once, the same event at cosine 0.90, and whether it names the same party. | Every quote is verbatim in that page. The register facts on the cited paths are always listed. |
| 5 | What could happen to STE because of that connection? | The model writes one conditional sentence. | It cites a path, uses could, may, or might, states no certainty, severity, likelihood, or probability, and adds no number the context lacks. With no path there is no hypothesis. |
| 6 | How confident are we? | A rule. | The model does not report it. |

**Confidence.** The rule reads logged facts and says how sure the code is of the link between the event and the STE register. It says nothing about outcomes.

| Level | When |
|---|---|
| `HIGH` | A direct or counterparty link, a firm name in the title or lead (register, reviewed, overlay, or derived), a path stated in the workbook, and at least one other outlet that carries the same event. |
| `MEDIUM` | A direct, counterparty, or competitor link with a firm name or another outlet, and a path at least derived. A perimeter link with another outlet also qualifies. |
| `LOW` | Anything else that is linked, such as a sector link or a generated name alone. |
| `NOT_ESTABLISHED` | No path from the signal reaches an STE node, or the identity check ended the link. |

A customer or supplier whose name begins with `Ministry`, `Department`, or `Bureau` stays below `HIGH`. Those names recur across countries, and the register states no country for a customer.

**Identity check.** The model first judges whether the party in the story is the register's party. A parent company, subsidiary, or regional unit counts as the same party. It answers `YES`, `UNSURE`, or `NO`. A `NO` needs a quote from the text that points elsewhere, and the code confirms the quote is there. An `UNSURE` with a verified quote caps the confidence at `LOW`. An `UNSURE` without one keeps it below `HIGH`. The answer can only lower the confidence. A `NO` or a quoted `UNSURE` takes the finding out of the priority list and into "Links the model doubted". In the 2026-10-06 sample, this check removed a story about a Delhi police officer that had matched `MINISTRY OF HOME AFFAIRS`.

The command writes `findings.json`, which holds each card and every iteration's draft, violations, issues, and response hash, and `loop-log.jsonl`. It rewrites `findings.json` after each finding with the status `IN_PROGRESS`, so an interrupted run keeps what the paid calls made, and it sets `DRAFT_FOR_REVIEW` at the end. It deletes an older `findings.json` first, and `report.run` refuses a file that is not finished. On the 2026-10-06 sample, 10 findings took about 2 minutes, with 34 to 36 model calls and 2 follow-up searches. Model calls use a low reasoning level, which keeps a call near 4 seconds.

```bash
.venv/bin/python -m loop.run --run output/brave-plan/2026-10-06 --dry-run
.venv/bin/python -m loop.run --run output/brave-plan/2026-10-06
```

## Weekly report

`report/run.py` drafts the weekly report from a filtered run. The model proposes text. Deterministic code validates it and renders the document.

When `findings.json` is in the run folder, the report has these sections in order: the priority findings (the cards from the loop), "Links the model doubted", an overview of the other signals, the other linked signals, and "No STE link established". Pass `--no-findings` to summarise every kept signal in the first report format. A `findings.json` that is unfinished or from another run date stops the command.

For the signals without a card, the model receives the kept signals with stable ids and never sees a URL. It writes an overview, one theme for each LOB group, and one summary for each signal, as JSON. The renderer builds the Markdown. Titles, dates, URLs, the order, and the line that says why a signal is linked come from the signal records, not the model. Other linked signals are grouped by LOB, then "Linked by name", then "Theme-level".

Validation rejects model text that:

- cites a signal id that does not exist, or a theme whose signals do not share its LOB label;
- contains a figure that is not in the signal's own text;
- contains a URL;
- uses the words severity, likelihood, probability, risk score, or risk rating.

A draft that fails goes back to the model once with the list of violations. If a part still fails, the report withholds that part, shows a visible placeholder, and lists it under "How this was produced". A draft with no usable structure is rejected and saved as `report.rejected.json`. Every report has the status `DRAFT_FOR_REVIEW`. Nothing approves or publishes it.

The command writes `report.md` and `report.json`. The JSON records the model, the prompt version, the response hash, the attempts, and what validation withheld.

```bash
.venv/bin/python -m report.run --run output/brave-plan/2026-10-06 --dry-run
.venv/bin/python -m report.run --run output/brave-plan/2026-10-06
```

> **Note:** Brave's `description` can lose punctuation, such as `US 82 2 Billion`, while its `extra_snippets` keep it. The report gives the model up to 2,000 characters of each signal's text so that it sees both. A figure that appears in neither form fails validation.

## Repository map

| Path | Role |
|---|---|
| `grit_scan/` | Harm scorer, ontology loader and graph, query plan, link ladder, document types, scale cues, briefing priority, BA gap export, weekly cascade, wild-scan labels, theme layer, guards. |
| `grit_scan/matchers/` | Credibility, exposure, LOB, and theme binding. |
| `acquisition/` | Candidate adapters. GDELT export and Currents are the live collectors. |
| `dataverse/` | App-only gateway and the three write commands. |
| `supervisor/` | In-memory ledger, package review, and the theme register. |
| `loop/` | The finding loop: ontology context, card checks and confidence rule, the loop engine, the follow-up search tool, and the command. |
| `report/` | Report drafting: model input, validation, the finding-card renderer, and the Markdown renderer. |
| `retrieval/` | Embedding client, vector cache, cosine similarity, ontology anchors, and the signal filter. |
| `agents/` | Four proposers, Vertex client, and the live agent pass. |
| `ui/` | Streamlit review screen. Sample package plus a Currents list. |
| `config/` | `urs` and `urs-tw` registers, the hand-authored `urs` LOB hierarchy and relationships overlay, plus `registry_aliases.json`. |
| `specs/` | Governing specifications. Spec 34 is the build order. |
| `tests/` | Contract tests. The runner is `run-impact-contract-tests.py`. |
| `secrets/URS_Entity_Info_20261006.xlsx` | Workbook for the `urs` pack and the transmission registry. Confidential; gitignored, not committed. `GRIT_WORKBOOK` selects another file. |

`requirements.txt` installs `msal`, `requests`, `streamlit`, and `google-genai`. The xlsx and docx readers use the standard library. `grit_scan/local_env.py` parses `.env`. It does not import `python-dotenv`, and it does not override a variable already set in the process.

## What is not wired

These are current gaps, not a backlog ranking.

1. No command turns a GDELT or Currents candidate into the JSON the weekly cascade consumes.
2. The harm scorer never runs on a row that Dataverse stores.
3. The review screen cannot approve a live Dataverse signal. Its buttons act on the sample package.
4. Dataverse workflow rows stop at `NEW`. `FETCHED` through `APPROVED` exist only in memory.
5. The `urs` pack has no Layer 1 conditions, and weekly import defaults to that pack.
6. Wild-scan labels always use the Taiwan registers.
7. Weekly priority still contains one Taiwan-specific theme bonus.
8. Source tiers for the harm scorer are not governed. The `urs` news sheets name outlets and do not rate them. One wire story carried by three papers still counts as three outlets.
9. The financial dimension stays `INSUFFICIENT_DATA` until a value-at-risk register exists.
10. The theme and event register is not persisted, and the horizon pack is not rendered.
11. Spec 34's byte-identical `2026-09-21` `revision.json` check is not proven here. Python JSON will also not match Windows PowerShell `ConvertTo-Json` byte for byte.
12. Acquisition still supplies prose. Without fatality counts, injury severity, or exact registered entity names, `assess_impact()` returns `NOT_ASSESSED` or `AMBIENT`.
13. No code reads `Risk Universe`, `Reports & Orgs`, or the `URS` capability list. The org chart image is used only through the hand-written `URS-LOB-Hierarchy.json`.
14. The pipeline works on files under `output/`. No command writes its signals or report to Dataverse, and the report is not connected to `ReviewGate()` or the Streamlit screen.
15. The pipeline links with `grit_scan/link.py`. `grit_scan/bind.py` is the earlier register-only bind and no command calls it. No code loads `patterns.yaml`, `risk_weights.yaml`, or `risk_crosswalk.yaml`: they are drafts, and the environment has no YAML library.
16. The workbook states too few relationships for cross-entity reasoning. `URS-Relationships.json` has no rows yet. Until the BA answers the export, a customer reaches the business area, a supplier reaches a unit, and 30 entities reach their LOB grouping only. See [Hand the gaps to the BA](#hand-the-gaps-to-the-ba).
17. Link precision depends on names. A generated name cannot tell two companies with one name apart, and a ministry name is not tied to a country. The identity check in the loop catches some of these, and only for the findings that reach a card.
18. The register's risk taxonomy, the sheet 7 classes and the hidden `Risk Universe`, does not type an event. Priority uses the 14 STE themes, the scale cues, and the link. A direct signal with no theme hit and no cue is tier 3.
19. The default bar for a big event, a KEY theme hit plus two cues, produced no big event in the 84-query sample. Set `--min-cues 1` to widen it. The KEY-04 keyword `cybersecurity` is a common word, and it opens the gate for policy and product stories.
20. The loop's follow-up search finds other outlets for one event. It does not search for the party's own statements, filings, or registers.

## Governing specifications

Specs 13–33 state the rules. Spec 34 states the rebuild order. Read the spec before changing the behaviour it names.

| Spec | Subject |
|---|---|
| 13, 14 | Annual risks and risk definitions. A taxonomy tag is not a multiplicand. |
| 15 | Severity. Maximum across dimensions. Financial uses value-at-risk only. |
| 16 | Likelihood. Annual assessment only. Not part of signal scoring. |
| 17, 20, 19, 18 | Supervisor, `TransitionGuard()`, `ReviewGate()`, and the append-only workflow table. |
| 21–26, 31 | Horizon, themes, events, escalation, reader aperture, and the reporting pack. |
| 27 | Dataverse model and phase plan. |
| 28 | MVP demo plan. Not a description of this repository. |
| 29, 30 | News priority and source credibility. |
| 32, 33 | Cascade from global conditions to an entity, and the URS-TW scanner shape. |
| 34 | Rebuild order. Treat section 5 as a gap list, then check this page for what later commits wired. |

## Install the local environment

Use the project virtual environment. System Python does not have `google-genai`, and two agent credential tests then fail before the refusal message is reached. Do not install packages into system Python. Do not apply operating-system updates on the shared host.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Copy `.env.example` to `.env` when you need a live call. Git ignores `.env`, `secrets/`, and `output/`.

## Provide the workbook

The `urs` ontology comes from one Excel workbook. Git ignores `secrets/`, so the file is never committed. The path is defined once, in `grit_scan/workbook.py`.

1. Copy the workbook to `secrets/`.
2. Name it `URS_Entity_Info_20261006.xlsx`. To use another name or folder, set `GRIT_WORKBOOK` to the file's path in `.env` or in the shell.
3. Rebuild the `urs` pack. This overwrites the eight generated registers in `config/urs/`. It does not change `URS-LOB-Hierarchy.json`.

   ```bash
   .venv/bin/python -m grit_scan.build_urs_pack
   ```

4. Run the contract tests, then run `git diff --stat -- config/urs` to review what changed.

## Run the weekly pipeline

Run these commands in order from the repository root. Replace `DATE` with the run date, `YYYY-MM-DD`. Each command writes into `output/brave-plan/DATE/`.

> **Warning:** Step 2 spends Brave queries. Steps 3, 5, and 6 call Vertex. Step 5 can also spend up to 10 Brave queries on follow-up searches. Run `--dry-run` first, and check the Brave dashboard before you run the full plan.

1. Rebuild the `urs` pack when the workbook changed.

   ```bash
   .venv/bin/python -m grit_scan.build_urs_pack
   ```

2. Search with the query plan. Add `--limit-queries 84` for a sample that tries every lane.

   ```bash
   .venv/bin/python -m acquisition.brave_plan --date DATE
   ```

3. Embed the candidates and score them against the ontology anchors.

   ```bash
   .venv/bin/python -m retrieval.embed_run --run output/brave-plan/DATE
   ```

4. Filter to the remaining signals.

   ```bash
   .venv/bin/python -m retrieval.filter_run --run output/brave-plan/DATE
   ```

5. Write the finding cards.

   ```bash
   .venv/bin/python -m loop.run --run output/brave-plan/DATE
   ```

6. Draft the report.

   ```bash
   .venv/bin/python -m report.run --run output/brave-plan/DATE
   ```

Between runs, hand the gaps to the BA and import their answers. The next filter and loop run read them.

## Run a check

From the repository root, with the virtual environment:

```bash
.venv/bin/python run-impact-contract-tests.py
.venv/bin/python -m grit_scan.ontology --pack urs
.venv/bin/python -m grit_scan.query_plan --date 2026-10-12
.venv/bin/python -m acquisition.brave_plan --date 2026-10-12 --dry-run --limit-queries 84
.venv/bin/python -m retrieval.embed_run --run output/brave-plan/2026-10-06 --dry-run
.venv/bin/python -m retrieval.filter_run --run output/brave-plan/2026-10-06
.venv/bin/python -m loop.run --run output/brave-plan/2026-10-06 --dry-run
.venv/bin/python -m report.run --run output/brave-plan/2026-10-06 --dry-run
.venv/bin/python -m grit_scan.relationship_gaps export
.venv/bin/streamlit run ui/app.py
```

The contract runner registers every `test_*` function in `tests/`. As of 2026-10-06, this virtual environment reports `PASS total=362`.

> **Warning:** The commands in the next block call the network. A Currents command without `--dry-run` creates Dataverse rows. Weekly import without `--dry-run` creates or patches rows. A GDELT export command does not write, because that file has no headline. Do not commit `.env`, `secrets/`, or a service-account JSON, and do not paste credential values into chat.

```bash
.venv/bin/python -m dataverse.run_wild_scan --query "Taiwan Strait" --max-records 10
.venv/bin/python -m dataverse.run_wild_scan --currents --query "Taiwan Strait" --max-records 10 --dry-run
.venv/bin/python -m dataverse.run_weekly_import --ranking output/urs/ranking.json --pack urs --dry-run
.venv/bin/python -m dataverse.backfill_wild_scan_relevance --dry-run
.venv/bin/python -m agents.run_pass
```

Rebuild the Urban Solutions pack only when the workbook is the source you intend to publish:

```bash
.venv/bin/python -m grit_scan.build_urs_pack
```

## Credentials

Put credentials in the gitignored `.env` at the repository root. Names are in `.env.example`.

Dataverse requires `DATAVERSE_TENANT_ID`, `DATAVERSE_CLIENT_ID`, `DATAVERSE_CLIENT_SECRET`, and `DATAVERSE_ORG_URL`. `DATAVERSE_ORG_URL` must be the environment root, such as `https://yourorg.crm5.dynamics.com`, with no path and no `/.default` suffix.

Currents requires `CURRENTS_API_KEY`. Brave requires `BRAVE_SEARCH_API_KEY`, and `BRAVE_MONTHLY_LIMIT` sets the local monthly guard.

Vertex configuration is `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_APPLICATION_CREDENTIALS`, and `GEMINI_MODEL`. The example file sets the location to `global` and the model to `gemini-3.8-flash`. The client falls back to `gemini-2.5-flash` when `GEMINI_MODEL` is empty. A missing credential refuses before any Google call.

## What this repository leaves behind

PowerShell repair scripts, demo output, mail exports, Power Automate flows, device-code authentication, and the Groq or Llama proposer are not part of this platform. Do not import them. Do not import a service-account key that was pasted into chat.
