# GRIT-Scan

GRIT-Scan tracks external risks that could affect ST Engineering's business performance. Every week it searches the web for each reader's standing conditions, named projects, suppliers, customers, and hazards. It binds what it finds to the STE register and writes each executive a brief in Word, PowerPoint, and Excel. A dashboard with a chat sits on top.

This repository is the proof of concept (POC) for the Group Head of Risk & Assurance (Wilson) and the Urban Solutions (URS) R&A business partner (Hwaimay). The first reader pack is URS Taiwan. The POC is the baseline for the MVP, which follows Specifications-Driven Development: [spec 35](specs/35_GRIT_Scan_POC_Architecture.md) describes the system as built, and [specs/README.md](specs/README.md) lists the governing specs.

## The rule

Agents propose. Deterministic functions decide. A named human approves.

- No scoring path calls a language model. Confidence, news priority, trend labels, RED status, and the harm verdict are rules.
- A missing fact stays missing. The brief prints "not established" or "nothing found". It never fills a gap with a default.
- A draft that cites no source, quotes a source inexactly, or states a number its source lacks is withheld, not published.
- Nothing is sent or published by code. A RED alert is a draft email. A weekly pack waits for `ReviewGate()`.

## How it works

GRIT-Scan follows the M365 Copilot ontology method, turned around. Copilot asks and then retrieves from a graph. GRIT-Scan pulls the web with keywords, then binds what it pulled to the register. Keywords decide what is fetched; the register decides what belongs to STE.

A deterministic layer sits under six agent roles:

| Role | Proposes | Decided by |
|---|---|---|
| Supervisor | The order of steps for a reader and week. | `TransitionGuard()` on each workflow advance, and `ReviewGate()` on each package. |
| Researcher | Search results, and one follow-up search for a thin finding. | The query plan, the monthly search guard, and the dedupe rule. |
| Assessor | A draft for each standing condition, and the answers on a finding card. | Quote, number, citation, and banned-word checks. Confidence is a rule. |
| Critic | Stale, unsupported, or contradictory claims in the drafts. | An issue counts only with a verbatim quote from a supplied source. |
| Profiler | The reader's preferences from their approve and reject decisions. | A bounded memory rule. It reorders within a tier and never hides a P1 or RED signal. |
| Reporter | Entries for named items, the bottom line, and chat answers. | The same checks, then one brief model rendered to every format. |

One weekly run for a reader pack has these stages. Each stage reads and writes the signal pool, so you can rerun one stage alone.

1. **Plan.** The reader pack's scan plan becomes queries: three per standing condition, one or two per named project, supplier, customer, and hazard, one per watchpoint, and up to two per active management request.
2. **Acquire.** Brave News answers each query once per run. Each page becomes one signal, keyed by its canonical URL.
3. **Embed.** Gemini embeds each page once. Vectors are cached by text hash.
4. **Assess.** With no model: document type, the link ladder, reader-pack hits, the keep rule, briefing priority, the risk cascade, the harm scorer, relevance labels, and the RED rule.
5. **Agents.** The Assessor drafts each condition, the Critic reviews the drafts, and the Reporter writes the named-item entries and the bottom line. Watchpoints update by rule. The finding loop writes cards for the strongest register-linked signals.
6. **Report.** Each reader's routing and memory pick and order the signals. The brief renders to Word, PowerPoint, Excel, and Markdown, and enters review.

## Repository layout

| Path | Contents |
|---|---|
| `grit/core/` | Pure deterministic functions: the harm scorer, `TransitionGuard()`, `ReviewGate()`, priority, scale cues, document type, routing, the RED rule, the confidence rule, the reader keep rule, and the condition cascade. |
| `grit/ontology/` | Reader packs, the typed graph, the link ladder, the reader index, the risk cascade, both query plans, workbook readers, and matchers. |
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
| `config/` | Registers: `urs/` and `urs-tw/` packs, `profiles/`, `risk/`, `red-patterns.json`, and `registry_aliases.json`. |
| `specs/` | Governing specifications. |
| `docs/` | Design notes and drafts (`design/`), and the description and reference briefs (`reference/`). |
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

   As of 2026-10-07, 380 tests pass. They make no network call.

## Provide the workbook and credentials

The `urs` pack is built from the confidential URS Entity Info workbook. Git ignores `secrets/`, `output/`, `.env`, and every `.xlsx` file.

1. Copy the workbook to `secrets/URS_Entity_Info_20261006.xlsx`. To use another path, set `GRIT_WORKBOOK`.
2. Copy `.env.example` to `.env` and fill in the values you need:
   - Gemini on Vertex: `GOOGLE_CLOUD_PROJECT` and `GOOGLE_APPLICATION_CREDENTIALS` for a service-account file, or `GOOGLE_API_KEY` for express mode. `GEMINI_MODEL` names the model.
   - Search: `BRAVE_SEARCH_API_KEY`, and optionally `CURRENTS_API_KEY`. `BRAVE_MONTHLY_LIMIT` sets the local monthly guard (default 2000).
   - Dataverse: `DATAVERSE_TENANT_ID`, `DATAVERSE_CLIENT_ID`, `DATAVERSE_CLIENT_SECRET`, and `DATAVERSE_ORG_URL` (the environment root, such as `https://yourorg.crm5.dynamics.com`).
3. Optional: rebuild the `urs` pack when the workbook changes, then review the diff.

   ```bash
   .venv/bin/python -m grit pack-build
   git diff --stat -- config/urs
   ```

Do not commit `.env`, `secrets/`, or a service-account file, and do not paste credential values into chat.

## Run the weekly scan

Run these commands from the repository root. `DATE` is the run date, `YYYY-MM-DD`; it defaults to today. `--pack` defaults to `urs-tw`.

> **Warning:** The scan spends Brave queries: about 60 per reader pack per week. Only the local counter in `output/brave-usage.json` enforces the monthly limit, and it ignores queries sent outside this repository. Check your plan in the Brave dashboard before a full run.

1. Print the queries. This sends nothing.

   ```bash
   .venv/bin/python -m grit weekly --date DATE --dry-run
   ```

2. Run the whole week: scan, agents, finding cards, packs for every reader, and RED drafts.

   ```bash
   .venv/bin/python -m grit weekly --date DATE
   ```

   To run the stages one at a time, use `grit scan`, `grit agents`, and `grit report` with the same `--date`. `grit assess` reruns the deterministic assessment with no network call. A query already answered in a run is not sent again, so an interrupted scan resumes.

3. Open the packs. Each reader's files are in `output/runs/DATE-PACK/PROFILE/vN/`: a Word brief, a PowerPoint deck, an Excel signal list, a Markdown copy, and the brief model as JSON. RED drafts are in `output/runs/DATE-PACK/alerts/`.

The Word brief follows the 2 Oct 2026 reference brief in `docs/reference/`: a bottom-line box, the order-book baseline, named projects, suppliers, customers, natural hazards, the ten standing conditions with trend, confidence, and dated sources, the QA and correction log, and the watchpoints.

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
| `wilson` | Group Head, Risk & Assurance | Wide | Reviewer | URS Taiwan brief, as in the reference. |
| `hwaimay` | R&A business partner, URS | Material | Reviewer | Adds priority findings and other signals. |
| `gareth` | Head, URS | Material | BA head | Short brief. Approves for AIRIS. |
| `ksyong` | Head, URS Taiwan | Material | Reader | Named items and conditions. |
| `group-cfo` | Group CFO | Wide | Reader | RED alerts only. |

A wide-aperture reader sees every kept signal. A material-aperture reader sees a signal only with P1, P2, or P4 priority, or a standing-condition match (spec 24). News priority is P1 for an active management request, P2 for a direct exposure path, P3 for a competitor, sector, or theme match, and P4 for a customer (spec 29).

Memory counts the reader's latest decision on each signal against its features: themes, register names, LOB, conditions, reader-pack items, and outlet. It moves a signal by at most three per feature, inside its tier. It hides a signal the reader rejected, and an outlet the reader rejected three times and never approved. It never hides a P1 or RED signal.

## Registers

| Register | What it holds |
|---|---|
| `config/urs/` | The Urban Solutions pack, built from the workbook: 41 entities, customers, suppliers, 201 competitors, the LOB hierarchy and keyword library, 14 STE themes, 130 sources, and management focus. `URS-Relationships.json` holds BA-approved answers to the graph's gaps. |
| `config/urs-tw/` | The Taiwan reader pack: ten standing conditions, named projects, customers, suppliers and sites, 26 rated sources, the scan plan (`URS-TW-Scan-Plan.json`), and order-book anchors (`URS-TW-Order-Book.json`). |
| `config/risk/` | The Group risk universe (101 risks and their keywords), and `Risk-Cascade.json`, which orders entity, BA, and Group registers by weight and crosswalks BA themes to Group codes. Every crosswalk row is proposed until R&A approves it. |
| `config/red-patterns.json` | The small RED patterns. |
| `config/profiles/` | Reader profiles. |

To add a reader pack for another business area or entity, add a pack folder with the same registers and a scan plan. It is a data change, not a code change.

## Deterministic rules

**Link ladder** (`grit/ontology/link.py`). A page lands on one rung, strongest first: `DIRECT` (names an STE entity, project, or site), `COUNTERPARTY` (a registered customer or supplier), `COMPETITOR`, `PERIMETER` (a LOB keyword and a country with an entity of that LOB), `SECTOR` (a LOB keyword only), or `THEME` (a big event with no register link). A name counts only in the title, in the lead, or named twice.

**Reader index** (`grit/ontology/reader_index.py`). It finds the reader pack's named items with the same discipline, plus rules learned on live data:

- A name followed by another capitalized word is a different name. "Chan Chun Sing" is not the supplier Chan Chun.
- A supplier, customer, or site needs a Taiwan or rail cue. Global Leonardo news is a passing mention.
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

The earlier digest covers the whole `urs` ontology on a 12-week query cycle and writes a Markdown digest with finding cards. It works on run folders under `output/brave-plan/`, not the pool. Its commands are `grit digest-plan`, `digest-search`, `digest-embed`, `digest-filter`, `digest-loop`, and `digest-report`; each takes `--help`. The reader scan reuses its link ladder, priority rule, and finding loop.

## Known gaps

These are the open items for the MVP, not a ranking.

1. Sphera supplier alerts and GDELT are not wired into the reader scan. Sphera has credentials in `secrets/` and no documented API contract here.
2. Severity stays not established. No source supplies structured observations, and no value-at-risk register exists.
3. Only the 26 Taiwan sources are rated. Most business-area sources are `UNRATED`.
4. The order-book anchors are copied from the reference brief. A named BA owner must confirm them.
5. The BA relationship gaps limit entity-level paths (see the previous section).
6. Nothing schedules the weekly run, sends mail, or authenticates dashboard users. The workflow ledger is written to the pool, not to Dataverse.
7. Only the URS Taiwan reader pack has a scan plan.

## Specifications

Read [specs/README.md](specs/README.md) before you change behavior a spec names. [Spec 35](specs/35_GRIT_Scan_POC_Architecture.md) describes this POC and lists what the MVP must specify.
