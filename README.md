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

The repository holds four products that share types and a Dataverse gateway. They do not form one pipeline.

| Path | What it decides | What it writes | What it does not do |
|---|---|---|---|
| Harm scorer, `grit_scan/impact_scoring.py` | Evidence, horizon, severity, transmission, high-negative-impact verdict, opportunity cost | Nothing. It is a pure function. | It is not called by the weekly cascade, the wild scan, the Dataverse import, or the agents. |
| Weekly cascade, `grit_scan/weekly_pipeline.py` | Condition match, exposure and theme binding, communication priority, Trend, Risk Level, Basis of Relevance | Local `ranking.json` and a text delta. A separate command can copy the ranking into Dataverse. | It does not scrape, and it does not call the harm scorer. |
| Wild scan, `dataverse/run_wild_scan.py` | Whether a headline can be stored, then a best-effort URS-TW theme label | Currents headlines, as `NEW` signals. GDELT export hits are printed and not stored. | It does not score harm, fetch an article body, or advance past `NEW`. |
| Review shell, `supervisor/` and `ui/` | `TransitionGuard()` and `ReviewGate()` | An in-memory ledger. The screen's approve action does not write Dataverse. | The live import paths do not run this chain to `APPROVED`. |

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

The transmission registry comes from `URS Entity Info.xlsx` plus `config/registry_aliases.json`, through `grit_scan/registry_loader.py`. The severity definition is parsed from `specs/15_Severity_Definition.md`. `grit_scan/inspect_severity.py` writes that parse as JSON. `grit_scan/backtest_shadow_mode.py` runs the scorer against an audited-signals export and prints a comparison. It does not write the workbook, and it does not treat the human audit as ground truth. The audited export has no structured observations, no value-at-risk, and no expected impact date, so those fields stay absent.

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
| `urs` | Urban Solutions. This is the default for weekly import. | `URS-LAYER1-ABSENT`. The condition list is empty. No Taiwan condition is copied. | `python3 -m grit_scan.build_urs_pack`, from `URS Entity Info.xlsx`. |
| `urs-tw` | Taiwan reader pack. | Ten governed baseline conditions, including their `taiwanTransmission` sentences. | Copied from the archived production registers. Not rebuilt by `build_urs_pack`. |

An empty `urs` baseline means `import_scan()` cannot accept a development for that pack: every `conditionHint` is unmatched. Weekly import still defaults to `urs`. Pass `--pack urs-tw` only when the ranking was produced from the Taiwan baseline.

The `urs` source register lists outlets from the workbook News sheet. It does not rate them. An unmatched outlet stays `UNRATED`. The workbook has no project sheet and no site register. Those lists stay empty. Country of incorporation is not treated as a priority country.

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

## Repository map

| Path | Role |
|---|---|
| `grit_scan/` | Harm scorer, ontology loader, weekly cascade, wild-scan labels, theme layer, guards. |
| `grit_scan/matchers/` | Credibility, exposure, LOB, and theme binding. |
| `acquisition/` | Candidate adapters. GDELT export and Currents are the live collectors. |
| `dataverse/` | App-only gateway and the three write commands. |
| `supervisor/` | In-memory ledger, package review, and the theme register. |
| `agents/` | Four proposers, Vertex client, and the live agent pass. |
| `ui/` | Streamlit review screen. Sample package plus a Currents list. |
| `config/` | `urs` and `urs-tw` registers, plus `registry_aliases.json`. |
| `specs/` | Governing specifications. Spec 34 is the build order. |
| `tests/` | Contract tests. The runner is `run-impact-contract-tests.py`. |
| `URS Entity Info.xlsx` | Workbook for the `urs` pack and the transmission registry. |

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
8. Source tiers for the harm scorer are not governed. The `urs` News sheet names outlets and does not rate them. One wire story carried by three papers still counts as three outlets.
9. The financial dimension stays `INSUFFICIENT_DATA` until a value-at-risk register exists.
10. The theme and event register is not persisted, and the horizon pack is not rendered.
11. Spec 34's byte-identical `2026-09-21` `revision.json` check is not proven here. Python JSON will also not match Windows PowerShell `ConvertTo-Json` byte for byte.
12. Acquisition still supplies prose. Without fatality counts, injury severity, or exact registered entity names, `assess_impact()` returns `NOT_ASSESSED` or `AMBIENT`.

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

## Run a check

From the repository root, with the virtual environment:

```bash
.venv/bin/python run-impact-contract-tests.py
.venv/bin/python -m grit_scan.ontology --pack urs
.venv/bin/streamlit run ui/app.py
```

The contract runner registers every `test_*` function in `tests/`. As of 2026-09-29, this virtual environment reports `PASS total=163`.

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

Currents requires `CURRENTS_API_KEY`.

Vertex configuration is `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_APPLICATION_CREDENTIALS`, and `GEMINI_MODEL`. The example file sets the location to `global` and the model to `gemini-3.8-flash`. The client falls back to `gemini-2.5-flash` when `GEMINI_MODEL` is empty. A missing credential refuses before any Google call.

## What this repository leaves behind

PowerShell repair scripts, demo output, mail exports, Power Automate flows, device-code authentication, and the Groq or Llama proposer are not part of this platform. Do not import them. Do not import a service-account key that was pasted into chat.
