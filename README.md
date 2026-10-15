# GRIT-Scan

GRIT-Scan tracks external risks that could affect ST Engineering's business performance. Every week it searches the web for each reader's standing conditions, named projects, suppliers, customers, and hazards. It binds what it finds to the STE register and writes each executive a brief in Word, PowerPoint, and Excel. A dashboard with a chat sits on top.

This repository is the proof of concept (POC) for the Group Head of Risk & Assurance (Wilson) and the Urban Solutions (URS) R&A business partner (Hwaimay). It runs two reader packs: Urban Solutions, the first business-area (BA) pack, and URS Taiwan, an entity pack under it. Wilson, Hwaimay, and the URS head (Gareth) receive both weekly briefs; the URS Taiwan head (KS Yong) receives the URS Taiwan brief. To add the other business areas, follow [Add a business area](docs/add-a-business-area.md). The POC is the baseline for the MVP, which follows Specifications-Driven Development: [spec 35](specs/35_GRIT_Scan_POC_Architecture.md) describes the system as built, and [specs/README.md](specs/README.md) lists the governing specs.

## The rule

Agents propose. Deterministic functions decide. A named human approves.

- No scoring path calls a language model. Confidence, news priority, trend labels, RED status, and the harm verdict are rules.
- A missing fact stays missing. The brief prints "not established" or "nothing found". It never fills a gap with a default.
- A draft that cites no source, quotes a source inexactly, or states a number its source lacks is withheld, not published.
- Nothing is sent or published by code. A RED alert is a draft email. A weekly pack waits for `ReviewGate()`.

## How it works

GRIT-Scan follows the M365 Copilot ontology method, turned around. Copilot asks and then retrieves from a graph. GRIT-Scan pulls the web with keywords, then binds what it pulled to the register. Keywords decide what is fetched; the register decides what belongs to STE.

A deterministic layer sits under seven agent roles:

| Role | Proposes | Decided by |
|---|---|---|
| Supervisor | The order of steps for a reader and week. | `TransitionGuard()` on each workflow advance, and `ReviewGate()` on each package. |
| Researcher | Search results, and one follow-up search for a thin finding. | The query plan, the monthly search guard, and the dedupe rule. |
| Assessor | A draft for each standing condition or theme, the implication of each key risk for the business area, and the answers on a finding card. | Quote, number, citation, and banned-word checks. An implication must name the business area's own lines of business, entities, customers, or suppliers. Confidence is a rule. |
| Critic | Stale, unsupported, or contradictory claims in the drafts. | An issue counts only with a verbatim quote from a supplied source. |
| Profiler | The reader's preferences from their approve and reject decisions. | A bounded memory rule. It reorders within a tier and never hides a P1 or RED signal. |
| Reporter | Entries for named items, the bottom line, and chat answers. | The same checks, then one brief model rendered to every format. |
| Proposer | Two to four options for management to consider on a key risk that is escalating or has material evidence. | Each option must name an owner role, quote the key control or action plan it builds on, and cite its sources. The Proposer's reasoning goes to the execution log, never to the brief (spec 36). |

## Reader packs

A reader pack is a folder under `config/` with a `pack.json` manifest. The manifest names the pack's level, its parent and children, and its register files.

| Pack | Level | Standing items | Named items | Weekly queries |
|---|---|---|---|---|
| `urs` | Business area | The 14 STE risk themes from workbook sheet 7: six key, five tier-2, and three emerging risks. | 41 entities, 20 customers, and 15 suppliers from the typed graph, plus competitors on an eight-week rotation. | About 120 |
| `urs-tw` | Entity, under `urs` | Ten standing conditions, from US-China competition to Taiwan's infrastructure resilience. | Named projects, suppliers, customers, sites, hazards, and watchpoints. | 60 |
| `dsc` | Business area | The 16 DS&C risk themes from workbook sheet 7: six key, five tier-2, and five emerging risks. | 21 entities, 30 customers, and 39 suppliers from the typed graph, plus competitors. | About 117 |
| `ste` | Group | Wilson's 12 dashboard themes as standing conditions, measured monthly against a baseline he signs off (spec 37). | None of its own: its footprint comes from its business-area children. | About 48 a month |

A BA brief summarises its entity packs that ran on the same date. Its Taiwan-concentration theme draws on the URS Taiwan pack's signals. Run entity packs first; `grit weekly` does that by default.

## Group pack

The Group pack (`ste`) briefs the CEO and the Chairman each month on the geopolitical and technology picture facing ST Engineering as a whole ([spec 37](specs/37_STE_Group_Pack.md)). It binds news through all its business-area children, so a signal can link on the footprint rung: a country where an STE entity is registered, named with one of the 12 standing conditions. For each condition with evidence, the Assessor writes the risk pathway, any opportunity, and the counter-indicator, and the Proposer writes options.

1. Write the pack and the two reader profiles. Existing files are kept.

   ```bash
   .venv/bin/python -m grit pack-init --pack ste
   ```

2. Add `"ste"` to `packs` in `config/profiles/wilson.json`, and add an `ste` brief: copy `briefs.ste` from `config/profiles/vschong.json` and set `preparedFor`.

3. Run the month. A monthly pack runs only when you name it, so `grit weekly` without `--pack` leaves it out.

   ```bash
   .venv/bin/python -m grit weekly --pack ste
   ```

4. Draft the baseline for Wilson's sign-off. The draft goes to `output/runs/DATE-ste/baseline-draft/`. After he signs off, copy each `currentAssessment` into `config/ste/STE-Global-Baseline.json` and set its `status` to `SIGNED_OFF`.

   ```bash
   .venv/bin/python -m grit baseline-draft --pack ste --date DATE
   ```

5. Each quarter, propose scenarios for the eight strategic shifts. A person edits them and saves them as `config/ste/STE-Scenarios.json`.

   ```bash
   .venv/bin/python -m grit scenarios --pack ste --date DATE
   ```

The monthly brief is the Group Strategic Radar: one row for each condition, with what changed, its sources, the risk and opportunity for STE, the business areas it touches, the counter-indicator, the options, and the trend. Wilson reviews it before it is released.

## State of the Situation

A State of the Situation (SotS) document explains one strategic topic in depth: its history, structural drivers, key actors, where things stand, the key uncertainties, and the indicators to watch. It is built only from the thought-leadership reports in the library, and every sentence cites a report page ([spec 38](specs/38_Report_Library_and_State_of_the_Situation.md)). It is the base for the Group pack's forecasts and options. There are 11 topics: Wilson's eight strategic shifts, plus China-Taiwan, defence budgets, and competitors.

1. Put the reports in `secrets/reports/`. `grit/library/catalogue.py` names each file, its publisher and its edition.

2. Read and embed them. A report that has not changed is skipped.

   ```bash
   .venv/bin/python -m grit library-ingest
   ```

3. Draft the four pilot topics: the Middle East, Ukraine, semiconductors and GPUs, and quantum-safe migration. Use `--topic` for others, or `--all`.

   ```bash
   .venv/bin/python -m grit sots
   ```

The drafts go to `output/sots/DATE/` as Word, Markdown, and JSON. To draft one section again, use `--redraft SECTION --from DATE`. To render again without a model call, use `--render-only DATE`. Wilson edits and signs off each document.

## Weekly run

One weekly run for a reader pack has these stages. Each stage reads and writes the signal pool, so you can rerun one stage alone.

1. **Plan.** The pack's scan plan becomes queries: for each standing condition or theme, for each active management request, for each named project, entity, supplier, customer, and hazard, and for each watchpoint. A BA pack takes its names from the typed graph and rotates the long lists.
2. **Acquire.** Brave News answers each query once per run. A query Brave leaves unanswered or empty goes to Currents, up to `CURRENTS_FALLBACK_MAX` (default 40) a run. Each page becomes one signal, keyed by its canonical URL.
3. **Embed.** Gemini embeds each page once. Vectors are cached by text hash.
4. **Assess.** With no model: document type, the link ladder, reader-pack hits, the keep rule, briefing priority, the risk cascade, the harm scorer, relevance labels, and the RED rule.
5. **Agents.** The Assessor drafts each standing condition or theme, the Critic reviews the drafts, and the Reporter writes the named-item entries and the bottom line. Watchpoints update by rule. The finding loop writes cards for the strongest register-linked signals.
6. **Report.** Each reader's routing and memory pick and order the signals. The brief renders to Word, PowerPoint, Excel, and Markdown, and enters review.

## Repository layout

| Path | Contents |
|---|---|
| `grit/core/` | Pure deterministic functions: the harm scorer, `TransitionGuard()`, `ReviewGate()`, priority, scale cues, document type, routing, the RED rule, the confidence rule, the reader keep rule, and the condition cascade. |
| `grit/ontology/` | Pack manifests and loader, the pack builder, the typed graph, the link ladder, the reader index, the risk cascade, both query plans, workbook readers, and matchers. |
| `grit/acquisition/` | Brave News, Currents, and GDELT adapters. They emit candidates and never score. |
| `grit/retrieval/` | Embeddings, the vector cache, similarity, and the business-area digest filter. |
| `grit/agents/` | The Gemini client, the reader agents, the chat agent, their checks, and the finding loop (`loop/`). |
| `grit/pool/` | The signal pool: `schema.sql` and its repository class. |
| `grit/pipeline/` | The reader scan stages and the harm-scorer adapter. |
| `grit/profiles/` | Reader profiles and the Profiler's memory. |
| `grit/report/` | The brief model and its Word, PowerPoint, Excel, and Markdown renderers, RED drafts, the AIRIS export, and the business-area digest. |
| `grit/workflow/` | The Supervisor, the theme register, and reader packages through review. |
| `grit/dataverse/` | The Dataverse gateway and the pool-to-`grit_signals` sync. |
| `grit/ui/` | The Streamlit dashboard and chat. |
| `grit/cli.py` | The command line: `python -m grit`. |
| `config/` | Registers: the `urs/` and `urs-tw/` packs with their `pack.json` manifests, `profiles/`, `risk/`, `red-patterns.json`, and `registry_aliases.json`. |
| `specs/` | Governing specifications. |
| `docs/` | The guide to adding a business area, design notes and drafts (`design/`), and the description and reference briefs (`reference/`). |
| `tests/` | Contract tests. |

## Install the local environment

Use the project virtual environment. Do not install packages into system Python, and do not apply operating-system updates on the shared host.

1. Create the environment and install the requirements.

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. Run the tests.

   ```bash
   .venv/bin/python -m pytest -q
   ```

   As of 2026-10-07, 389 tests pass. They make no network call.

## Provide the workbook and credentials

The `urs` pack is built from the confidential URS Entity Info workbook. Git ignores `secrets/`, `output/`, `.env`, and every `.xlsx` file.

1. Copy the workbook to `secrets/URS_Entity_Info_20261006.xlsx`. To use another path, set `GRIT_WORKBOOK`. Another business area's workbook has its own variable, such as `GRIT_WORKBOOK_DSC`.
2. Copy `.env.example` to `.env` and fill in the values you need:
   - Gemini on Vertex: `GOOGLE_CLOUD_PROJECT` and `GOOGLE_APPLICATION_CREDENTIALS` for a service-account file, or `GOOGLE_API_KEY` for express mode. `GEMINI_MODEL` names the model.
   - Search: `BRAVE_SEARCH_API_KEY`, and optionally `CURRENTS_API_KEY`. `BRAVE_MONTHLY_LIMIT` sets the local monthly guard (default 2000).
   - Dataverse: `DATAVERSE_TENANT_ID`, `DATAVERSE_CLIENT_ID`, `DATAVERSE_CLIENT_SECRET`, and `DATAVERSE_ORG_URL` (the environment root, such as `https://yourorg.crm5.dynamics.com`).
3. Optional: rebuild the `urs` pack when the workbook changes, then review the diff. The builder reads the pack's `pack.json` for its id prefix, keyword sheets, and LOB hierarchy file.

   ```bash
   .venv/bin/python -m grit pack-build --pack urs
   git diff --stat -- config/urs
   ```

Do not commit `.env`, `secrets/`, or a service-account file, and do not paste credential values into chat.

## Run the weekly scan

Run these commands from the repository root. `DATE` is the run date, `YYYY-MM-DD`; it defaults to today. Without `--pack`, every pack with a scan plan runs, entity packs first. To run one pack, add `--pack urs` or `--pack urs-tw`.

> **Warning:** The scan spends Brave queries: about 180 a week for both packs, or about 780 a month. `--sweep N` adds up to N queries from the 12-week business-area digest plan. Only the local counter in `output/brave-usage.json` enforces the monthly limit, and it ignores queries sent outside this repository. Check your plan in the Brave dashboard before a full run.

1. Print the queries. This sends nothing.

   ```bash
   .venv/bin/python -m grit weekly --date DATE --dry-run
   ```

2. Run the whole week: scan, agents, finding cards, packs for every reader, and RED drafts.

   ```bash
   .venv/bin/python -m grit weekly --date DATE
   ```

   To run the stages one at a time, use `grit scan`, `grit agents`, and `grit report` with the same `--date`. `grit assess` reruns the deterministic assessment with no network call. A query already answered in a run is not sent again, so an interrupted scan resumes.

3. Open the packs. Each reader's files are in `output/runs/DATE-PACK/PROFILE/vN/`: a Word brief, a PowerPoint executive summary in the format of `docs/reference/GRIT-Scan_Executive_Summary.pptx`, an Excel signal list, a Markdown copy, and the brief model as JSON. RED drafts are in `output/runs/DATE-PACK/alerts/`.

The URS Taiwan brief follows the 2 Oct 2026 reference brief in `docs/reference/`: a bottom-line box, the order-book baseline, named projects, suppliers, customers, natural hazards, the ten standing conditions with trend, confidence, and dated sources, the QA and correction log, and the watchpoints.

The Urban Solutions brief follows the same style at BA level: the bottom line, the 14 risk themes in register order (each key risk, and any escalating theme, as a row of the key-risk table: development, source, date and time, relevance to the business area, proposed response, and priority and trend; the rest as one row of the theme table), management-requested topics with the signals that matched each one, the URS Taiwan summary, STE entities by line of business, customer, supplier, and competitor watch, priority findings, the QA log, and the watchpoints. Each reader's profile chooses which of these sections they receive.

## Review packs and hand off to AIRIS

Each reader's pack is a package in the workflow. Every member signal runs `NEW` to `PENDING_REVIEW` through `TransitionGuard()`, in an execution of its own for that reader.

1. Record a reader's decision on a signal. This feeds the reader's memory and never changes the shared signal.

   ```bash
   .venv/bin/python -m grit feedback --profile wilson --signal SIGNAL_ID --decision APPROVE --rationale "Material to Taiwan rail"
   ```

2. Decide on a package. `ReviewGate()` decides; the Supervisor writes the ledger. Intents are `APPROVE`, `REQUEST_REWORK`, `HOLD`, and `REJECT`. A reader-only profile is refused.

   ```bash
   .venv/bin/python -m grit review --package PKG-DATE-urs-tw-gareth --version 1 --profile gareth --intent APPROVE
   ```

3. Export to AIRIS. Only signals that the BA head (Gareth) approved, inside an approved package, move to `AIRIS_HANDOFF_READY`. The export is JSON and Excel in `output/airis/`, and each row names the package version and hash.

   ```bash
   .venv/bin/python -m grit airis --dry-run
   .venv/bin/python -m grit airis
   ```

A changed brief cannot replace a package that is still pending. Return the pending version for rework, then write the pack again; the new version gets its own folder.

## Use the dashboard and chat

Start the dashboard, then open the URL it prints (port 8501 by default). From VS Code on a remote host, forward the port first.

```bash
.venv/bin/python -m grit ui
```

Pick a reader and a run in the sidebar. The tabs show:

- **Dashboard:** the bottom line, the ten conditions, named items, watchpoints, and the QA log.
- **Signals:** every signal in the reader's view, with why it is there, its link, risk register entries, harm verdict, and the reader's memory. Approve, reject, or watch a signal here.
- **Packs and review:** download the files, write this week's pack, and decide on a pending package.
- **Management requests:** propose a concern, topic, trend, or keyword. A reviewer activates it. An active request adds queries to the next scan and assigns P1 to matching signals.
- **RED and AIRIS:** RED drafts to send or dismiss, and the BA head's AIRIS export.

The chat sits under the tabs. It answers from the reader's own signals, cites them with links, and withholds an answer that fails its checks. Type `/concern TITLE: keyword, keyword` (or `/topic`, `/trend`, `/keyword`) to propose a management request.

## Reader profiles

Profiles are JSON files in `config/profiles/`. A profile changes what a reader sees and in what order, never a shared fact.

| Profile | Role | Aperture | Review role | Brief |
|---|---|---|---|---|
| `wilson` | Group Head, Risk & Assurance | Wide | Reviewer | URS and URS Taiwan. The Taiwan brief matches the reference. |
| `hwaimay` | R&A business partner, URS | Material | Reviewer | URS and URS Taiwan, with priority findings and other signals. |
| `gareth` | Head, URS | Material | BA head | Short URS and URS Taiwan briefs. Approves for AIRIS. |
| `ksyong` | Head, URS Taiwan | Material | Reader | URS Taiwan only. |
| `group-cfo` | Group CFO | Wide | Reader | RED alerts only. |
| `vschong` | Chief Executive Officer (Vincent S. Chong) | Material | Reader | The monthly Group Strategic Radar, after Wilson approves it. |
| `mkteo` | Chairman (MK Teo) | Material | Reader | The monthly Group Strategic Radar. He conveys his views to Wilson. |

A profile lists its packs in `packs` and one brief per pack under `briefs`. Each package is one reader's brief for one pack and one week. Add `RESPONSE_OPTIONS` to a brief's `sections` to show the Proposer's options under each key risk; it adds no section of its own.

A wide-aperture reader sees every kept signal. A material-aperture reader sees a signal only with P1, P2, or P4 priority, or a standing-condition match (spec 24). News priority is P1 for an active management request, P2 for a direct exposure path, P3 for a competitor, sector, footprint, or theme match, and P4 for a customer (spec 29).

Memory counts the reader's latest decision on each signal against its features: themes, register names, LOB, conditions, reader-pack items, and outlet. It moves a signal by at most three per feature, inside its tier. It hides a signal the reader rejected, and an outlet the reader rejected three times and never approved. It never hides a P1 or RED signal.

## Registers

| Register | What it holds |
|---|---|
| `config/urs/` | The Urban Solutions pack, built from the workbook: 41 entities, customers, suppliers, 201 competitors, the LOB hierarchy and keyword library, 14 STE themes, 130 sources, and management focus. `URS-Scan-Plan.json` holds the weekly theme queries, the sheet 10 management requests with their P1 keywords, the name rotation, and the brief's section titles. `URS-Relationships.json` holds BA-approved answers to the graph's gaps. |
| `config/urs-tw/` | The Taiwan reader pack: ten standing conditions, named projects, customers, suppliers and sites, 26 rated sources, the scan plan (`URS-TW-Scan-Plan.json`), and order-book anchors (`URS-TW-Order-Book.json`). |
| `config/risk/` | The Group risk universe (101 risks and their keywords), and `Risk-Cascade.json`, which orders entity, BA, and Group registers by weight and crosswalks BA themes to Group codes. Every crosswalk row is proposed until R&A approves it. |
| `config/red-patterns.json` | The small RED patterns. |
| `config/profiles/` | Reader profiles. |

To add a reader pack for another business area or entity, follow [Add a business area](docs/add-a-business-area.md). It is a data change, not a code change.

**Management requests.** The URS scan plan seeds sheet 10 into the pool. A numbered request, and a focus item marked "Management priority" or "Core control focus", start `ACTIVE`; a focus item marked "Research draft" starts `PROPOSED`. After that, only a reviewer changes a request's status. An active request's keywords make a matching signal P1. A keyword `A + B` needs every part in the title or lead, so `Qatar + work visa` does not match a tourist-visa story.

## Deterministic rules

**Link ladder** (`grit/ontology/link.py`). A page lands on one rung, strongest first: `DIRECT` (names an STE entity, project, or site), `COUNTERPARTY` (a registered customer or supplier), `COMPETITOR`, `PERIMETER` (a LOB keyword and a country with an entity of that LOB), `SECTOR` (a LOB keyword only), or `THEME` (a big event with no register link). A name counts only in the title, in the lead, or named twice.

**Reader index** (`grit/ontology/reader_index.py`). It finds the reader pack's named items with the same discipline, plus rules learned on live data:

- A name followed by another capitalized word is a different name. "Chan Chun Sing" is not the supplier Chan Chun.
- A supplier, customer, or site needs one of the pack's context cues; for URS Taiwan, a Taiwan place or a rail word. Global Leonardo news is a passing mention.
- In a BA pack, names come from the typed graph and the link ladder. A generic public-body name such as "MINISTRY OF DEFENCE" counts only with one of the pack's place cues, so India's Home Ministry is not a URS customer.
- In a BA pack, a theme keyword counts for that theme only on a page the theme's own query found, or on a page that already links to the register.
- A project alias without its city, such as "Purple Line", needs the city in the text and matches in its own case.
- A watchpoint update needs a subject keyword and an issue keyword.

**Keep rule** (`grit/core/reader_filter.py`). A page older than eight days is stale, and a market report or listicle is not an event. A page must link to the register, name a reader-pack item, bear on a condition or hazard, or hit a KEY theme. Pages with the same headline, or at cosine 0.95, are one story. A relevant page absorbs other outlets' pages at cosine 0.90, which then count as corroboration, with each wire service counted once.

**Briefing priority** (`grit/core/priority.py`). A big event hits a KEY theme and meets two of three cues: three or more outlets, a regulator or government action, a war, or a pandemic, and a stated figure of at least one million in a major currency. Tier 1 is big and direct, tier 2 is big or direct and significant, tier 3 is linked, and tier 4 is not linked.

**Confidence** (`grit/core/confidence.py`). `HIGH` needs two independent outlets and one rated `HIGH` in the reader's source register. `MEDIUM` needs two outlets or one `HIGH`-rated outlet. Otherwise it is `LOW`.

**RED** (`grit/core/red_alert.py`). A pattern must hit the title or lead, in a sentence that is not a forecast, an assessment, or a drill, and the event must be corroborated or carried by a `HIGH`-rated source. "Invasion of Taiwan unlikely before 2028" is not RED.

**Harm scorer** (`grit/core/impact_scoring.py`). It runs on every kept signal and returns evidence, horizon, severity, transmission, a verdict, and opportunity cost. Severity is the maximum across dimensions and is never averaged. Financial severity uses value-at-risk only. Acquisition supplies prose, not structured observations, so severity stays not established until those exist.

## Signal pool

The pool is one SQLite file, `output/grit.sqlite` (set `GRIT_DB` to move it). Its schema is [grit/pool/schema.sql](grit/pool/schema.sql). Shared facts (`signals`, `sightings`, `vectors`) are kept apart from reader-scoped records (`assessments`, `feedback`, `packages`). `workflow_states` is append-only: triggers refuse `UPDATE` and `DELETE`. Every agent and logged function call is in `execution_log` with its prompt version and hashes.

To print the row counts, run `.venv/bin/python -m grit db`.

Dataverse remains the system of record. To push a run's kept signals to `grit_signals` with existing columns and choice codes, run the following. It is a dry run unless you add `--push`. Vectors stay in the pool, because `grit_signals` has no vector column.

```bash
.venv/bin/python -m grit dataverse --date DATE
```

## Hand the gaps to the BA

The workbook states too few relationships for entity-level reasoning: 30 of 41 entities have no single LOB, no customer links to an entity, and a supplier links to a unit. Export the questions for the BA, then import the answers into `config/urs/URS-Relationships.json`. The CSV files hold names from the confidential workbook, so do not commit them.

```bash
.venv/bin/python -m grit gaps export
.venv/bin/python -m grit gaps import --relationships output/ba-review/relationships-to-fill.csv --aliases output/ba-review/aliases-to-review.csv --dry-run
```

## Business-area digest

The earlier digest covers the whole `urs` ontology on a 12-week query cycle and writes a Markdown digest with finding cards. It works on run folders under `output/brave-plan/`, not the pool. Its commands are `grit digest-plan`, `digest-search`, `digest-embed`, `digest-filter`, `digest-loop`, and `digest-report`; each takes `--help`. The reader scan reuses its link ladder, priority rule, and finding loop. To fold the digest into the weekly run instead, add `--sweep N` to `grit scan` or `grit weekly`: up to N queries due from the 12-week plan join the URS run, and their pages go through the same rules, brief, and review.

## Known gaps

These are the open items for the MVP, not a ranking.

1. Sphera supplier alerts and GDELT are not wired into the reader scan. Sphera has credentials in `secrets/` and no documented API contract here.
2. Severity stays not established. No source supplies structured observations, and no value-at-risk register exists.
3. Only the 26 Taiwan sources are rated. Most business-area sources are `UNRATED`.
4. The order-book anchors are copied from the reference brief. A named BA owner must confirm them.
5. The BA relationship gaps limit entity-level paths (see the previous section).
6. Nothing schedules the weekly run, sends mail, or authenticates dashboard users. The workflow ledger is written to the pool, not to Dataverse.
7. Urban Solutions, URS Taiwan, DS&C, and the Group pack have scan plans. The other business areas follow [Add a business area](docs/add-a-business-area.md). Until they do, the Group footprint covers URS and DS&C only.
8. Generated short names cannot tell two companies with one name apart. On 2026-10-07, "Amtech Systems" matched a US semiconductor company. Reject such aliases through `grit gaps`.

## Specifications

Read [specs/README.md](specs/README.md) before you change behavior a spec names. [Spec 35](specs/35_GRIT_Scan_POC_Architecture.md) describes this POC and lists what the MVP must specify.
