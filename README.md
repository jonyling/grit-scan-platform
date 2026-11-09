# GRIT-Scan

GRIT-Scan tracks external risks that can affect ST Engineering. Each week it searches for a reader's standing conditions, named projects, suppliers, customers, and hazards. It binds each page to the STE register. It writes that reader's brief in Word, PowerPoint, Excel, and Markdown. A dashboard and a Co-Pilot answer questions from the same records.

This repository is the proof of concept for Wilson, Group Head of Risk & Assurance, and the baseline for the MVP. [Spec 35](specs/35_GRIT_Scan_POC_Architecture.md) describes that proof of concept as first built, with Urban Solutions and URS Taiwan. The checkout now also holds Digital Systems & Cyber, Commercial Aerospace, Marine, and the Group pack. To add another business area, follow [Add a business area](docs/add-a-business-area.md). The governing specs are listed in [specs/README.md](specs/README.md).

## The rule

Agents propose. Deterministic functions decide. A named human approves.

The weekly command uses the compatibility path. `--reasoned` is the opt-in path in [spec 45](specs/45_Weekly_Agent_Reasoning_and_Critique.md). On that path, agents can change editorial inclusion. They cannot change a controlled score or an approval.

- Scoring stays in rules. Confidence, news priority, trend labels, RED status, and the harm verdict are functions in `grit/core/`.
- A missing fact stays missing. The brief prints "not established" or "nothing found".
- A draft with no source, an inexact quote, or a number the source lacks is withheld.
- A weekly pack waits for `ReviewGate()` before it counts as approved. A RED alert is a draft email. When Microsoft Graph mail is configured, a daily run sends that draft to the R&A duty team, and a named person forwards it. With no mail settings, the draft stays unsent.

## Repository layout

| Path | Contents |
|---|---|
| `grit/core/` | Deterministic functions: harm, `TransitionGuard()`, `ReviewGate()`, priority, document type, routing, RED, confidence, the keep rule, and the condition cascade. |
| `grit/ontology/` | Pack manifests, the pack builder, the typed graph, the link ladder, query plans, and workbook readers. |
| `grit/acquisition/` | Brave News, Currents, GDELT, and the closed Sphera lane. They emit candidates. They do not score. |
| `grit/retrieval/` | Embeddings, the vector cache, similarity, and the business-area digest filter. |
| `grit/agents/` | The model client, reader agents, checks, and the finding loop in `loop/`. |
| `grit/copilot/` | Co-Pilot scope, search, tools, and checks. Specs 40 and 41. |
| `grit/daily/` | The daily feed lane and the nightly index. Spec 42. |
| `grit/pool/` | The signal pool: `schema.sql` and the repository class. |
| `grit/pipeline/` | Reader-scan stages, the weekly run, scenarios, and the harm adapter. |
| `grit/profiles/` | Reader profiles, notes, emphasis, the Profiler, and the fit report. Specs 39 and 41. |
| `grit/report/` | The brief model and the Word, PowerPoint, Excel, and Markdown renderers. |
| `grit/workflow/` | The Supervisor, the theme register, and packages through review. |
| `grit/adverse/` | The adverse news and lessons learnt scan. Spec 46. |
| `grit/dataverse/` | The Dataverse gateway and the sync to `grit_signals`. |
| `grit/library/` | The report library and State of the Situation drafts. Spec 38. |
| `grit/ui/` | The Streamlit dashboard and the Co-Pilot drawer. |
| `grit/cli.py` | The command line: `python -m grit`. |
| `grit/schedule.py` | The Monday cadence for `grit scheduled`. |
| `config/` | Reader packs, profiles, the risk universe, adverse registers, and shared source ratings. |
| `specs/` | Governing specifications. |
| `docs/` | Guides, design notes, reference files, and progress records. See [docs/README.md](docs/README.md). |
| `tests/` | Contract tests. |

## Install the local environment

Use the project virtual environment. Install packages there. Leave system Python unchanged.

1. Create the environment and install the requirements.

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. Optional: install the `grit` command on the environment's path.

   ```bash
   .venv/bin/pip install -e . --no-deps
   source .venv/bin/activate
   ```

   After activation, `grit` runs the same entry point as `.venv/bin/python -m grit`.

3. Run the tests.

   ```bash
   .venv/bin/python -m pytest -q
   ```

   As of 8 November 2026, 888 tests pass. They make no network call. Set `GRIT_TEST_ROOT` to a disposable directory when a fixture run must leave this checkout's `config/`, `output/`, and `secrets/` unread. That switch also skips `.env`.

   ```bash
   GRIT_TEST_ROOT=/tmp/grit-weekly-fixtures .venv/bin/python -m pytest \
     tests/test_weekly_reasoning.py tests/test_p3_transition_guard.py \
     tests/test_p3_supervisor.py tests/test_p3_review_gate.py -q
   ```

## Provide the workbook and credentials

The scan reads workbooks from `secrets/` and keys from `.env`. Git ignores `secrets/`, `output/`, `.env`, and every `.xlsx` file. The Urban Solutions pack is built from the confidential URS Entity Info workbook.

1. Copy each workbook to `secrets/`. A file whose name carries the business area's tag is used when the manifest's own file name is missing. Examples are `02_URS_Entity_Info.xlsx`, `04_DSC_Entity_Info.xlsx`, `01_CA_Entity_Info.xlsx`, and `06_Marine_Entity_Info.xlsx`. The newest matching file wins. Set `GRIT_WORKBOOK` to choose another URS path. Set `GRIT_WORKBOOK_DSC` (and the same pattern for another area) to choose that area's path.

2. Copy `.env.example` to `.env` and fill in the values you use.

   - Gemini on Vertex: `GOOGLE_CLOUD_PROJECT` and `GOOGLE_APPLICATION_CREDENTIALS`, or `GOOGLE_API_KEY` for express mode. `GEMINI_MODEL` names the model.
   - Search: `BRAVE_SEARCH_API_KEY`, and optionally `CURRENTS_API_KEY`. `BRAVE_MONTHLY_LIMIT` is the local monthly guard. The default in code is 2000. Brave's free credit is about 1,000 queries a month.
   - DeepSeek, only for `--reasoner deepseek`: `DEEPSEEK_API_KEY`. `DEEPSEEK_MODEL` defaults to `deepseek-v4-flash`.
   - Dataverse: `DATAVERSE_TENANT_ID`, `DATAVERSE_CLIENT_ID`, `DATAVERSE_CLIENT_SECRET`, and `DATAVERSE_ORG_URL`.
   - URS Taiwan fire detections: `FIRMS_MAP_KEY`.

3. Optional: rebuild a pack after the workbook changes, then review the diff. The builder reads `pack.json` for the id prefix, keyword sheets, and line-of-business hierarchy file.

   ```bash
   .venv/bin/python -m grit pack-build --pack urs
   git diff --stat -- config/urs
   ```

Keep `.env`, `secrets/`, and any service-account file out of git. Keep those values out of chat.

## Run a weekly scan

Run these commands from the repository root. `DATE` is the run date, `YYYY-MM-DD`. It defaults to today.

`grit weekly` with no `--pack` runs every pack that has a scan plan and a window of 10 days or less. That set includes Commercial Aerospace and Marine. The Group pack is monthly, so it runs only when you name `--pack ste`. `grit scheduled` runs only the packs in `SCHEDULE`.

> **Warning:** A scan spends Brave queries. The Monday budget in [Run the Monday schedule](#run-the-monday-schedule) covers DS&C, Urban Solutions, URS Taiwan, and the Group pack. An unnamed `grit weekly` also searches Commercial Aerospace and Marine, including named customers and suppliers. A search already stored for that run date is reused. `BRAVE_MONTHLY_LIMIT` stops the local counter. The default is 2000. Brave's free credit is about 1,000 queries. Past that credit, Brave refuses with HTTP 402. The scan then falls back to Currents for at most `CURRENTS_FALLBACK_MAX` queries a run (default 40). The counter in `output/brave-usage.json` counts queries from this repository. To rewrite a brief, rerun `grit agents` and `grit report`. Those commands call the model and the renderers. `--sweep N` adds up to N queries from the 12-week business-area digest plan.

1. Print the queries. This sends nothing.

   ```bash
   .venv/bin/python -m grit weekly --date DATE --dry-run
   ```

2. Run the week: scan, agents, finding cards, packs for every selected reader, and RED drafts.

   ```bash
   .venv/bin/python -m grit weekly --date DATE --pack urs urs-tw
   ```

   To run the stages one at a time, use `grit scan`, `grit agents`, and `grit report` with the same `--date` and `--pack`. `grit assess` reruns the deterministic assessment with no network call. An interrupted scan resumes from queries already stored for that run. The run folder's `searches.jsonl` keeps every answer. A database that lacks a logged query's pages can reload them from that log.

3. Open the packs. Each reader's files are in `output/runs/DATE-PACK/PROFILE/vN/`: a Word brief, a PowerPoint executive summary, an Excel signal list, a Markdown copy, and the brief model as JSON. The deck follows `docs/reference/GRIT-Scan_Executive_Summary.pptx`. RED drafts are in `output/runs/DATE-PACK/alerts/`.

A business-area brief follows Wilson's layout in [spec 44](specs/44_Brief_Format_v2.md). It has a status line, the bottom line, escalating and elevated items, what changed, a 30-day look-ahead, a priority table, coverage, jurisdictions, and the named supplier, customer, and competitor watch. A quality check and a numbered source register close it. The body cites sources as [S1], [S2]. Approved scenarios appear after `grit scenarios` and `grit scenarios-approve` for that pack.

Each Word brief, Markdown preview, and slide says the content is GRIT-Scan AI-generated suggestions for consideration, and that a named person reviews it before anyone acts on it. A suggestion appears only when a proposed option or a triggered scenario response already cites that signal. It is marked "Suggestion for the business area to consider."

The URS Taiwan brief follows the 2 October 2026 reference in `docs/reference/`. The Urban Solutions brief uses the same style at business-area level, including the URS Taiwan summary when that entity pack ran on the same date. Each reader's profile chooses the sections.

One weekly run has six stages. Each stage reads and writes the signal pool, so you can rerun one stage alone.

1. **Plan.** The scan plan becomes queries for standing conditions, active management requests, named projects, entities, suppliers, customers, hazards, and watchpoints.
2. **Acquire.** Brave News answers each query once per run. A query Brave leaves empty goes to Currents, up to the fallback cap. Each page becomes one signal, keyed by its canonical URL.
3. **Embed.** Gemini embeds each page once. Vectors are cached by text hash.
4. **Assess.** The deterministic rules in `grit/core/` and the ontology linkers score each page. This stage calls no model.
5. **Agents.** The Assessor drafts each standing condition or theme. The Critic reviews the drafts. The Reporter writes the named-item entries and the bottom line. The finding loop writes cards for the strongest register-linked signals.
6. **Report.** Each reader's routing picks the signals. The reader profile orders them. The brief renders to Word, PowerPoint, Excel, and Markdown, and enters review.

## Use weekly reasoning

The compatibility path remains the default. To run the six-role path, pass `--reasoned` and an explicit countersearch limit. There is no numeric default.

```bash
.venv/bin/python -m grit weekly --pack PACK --date YYYY-MM-DD \
  --reasoned --countersearch-limit N --profile PROFILE
```

`N` is the nonnegative search budget for the run. You must pass it, including `0`. `0` turns countersearch off and the report says the top findings were left unchallenged. Each of the top five findings needs a search that tries to contradict it, so set `N` to at least 5. `10` leaves room for other checks. Requested checks that cannot run stay visible and block ordinary readiness.

To rank kept signals with DeepSeek, and to draft the report text with it, add `--reasoner deepseek`. Put `DEEPSEEK_API_KEY` in `.env`. Gemini still screens the pool. If a DeepSeek task fails, the Gemini selections remain.

```bash
.venv/bin/python -m grit weekly --pack PACK --date YYYY-MM-DD \
  --reasoned --reasoner deepseek --countersearch-limit N --profile PROFILE
```

Record a miss for the next run with `grit misses add`. `grit misses propose` writes a review file. It does not change the scan plan or the prompts.

A reasoned run makes two model calls for every 16 candidates. A pool of about 2,000 candidates takes 250 or more calls, roughly 1.5 hours or longer. Run it in a persistent terminal session.

To shorten the review, add `--fast`.

```bash
.venv/bin/python -m grit weekly --pack PACK --date YYYY-MM-DD \
  --reasoned --fast --countersearch-limit N --profile PROFILE
```

With `--fast`, the Critic screens deterministic drops from compact records, about 80 per call. Only the drops it doubts receive the full Assessment and Critic review. Up to six calls run at once. `--workers N` changes that number. Batches whose input has not changed reuse their earlier answers. A pool of about 2,000 candidates takes about 120 calls, roughly 10 to 15 minutes. Compare a fast review with a full reasoned run on the same date before you rely on it. Switching modes starts a new analysis version.

Assessment and an independent Critic account for deterministic keeps and drops, including acquired counterevidence. The Critic can rescue or exclude candidates. Reporting revises the existing sections. A final Critic checks the exact per-reader snapshot. Up to two reconciliation rounds are shared across the run and survive resume. Each report also gets one rewrite when the final Critic raises a material objection, even if the pool used both rounds. The Critic treats a missing internal check as material only when the report claims confirmed exposure or impact. An unresolved material objection blocks submission. A critical completeness-gate answer of "No" also blocks submission. The report is then `INCOMPLETE`, and the rendered gate names the blocker.

`grit report --pack PACK --date YYYY-MM-DD --no-submit` can render an incomplete preview. Word, PowerPoint, Excel, Markdown, and JSON use the frozen report. An incomplete analysis returns exit code 2 even when the preview is written.

> **Warning:** Rendering can overwrite report files in the run's output folder. Copy files you need to keep before you render again.

If a reasoned run stops on a Reporting call after the signal review, recover that stage without repeating search or the signal review:

```bash
.venv/bin/python -m grit resume-report --pack PACK --date YYYY-MM-DD
```

Recovery requires the saved evidence, critique, draft, and report inputs to remain unchanged. It uses the original budget. It uses the original model unless you pass `--model`. Open material issues still block readiness.

To rebuild every report from a finished review, add `--refresh`. Use it after an R&A RED decision or a report-stage fix. The evidence must be unchanged since the review. Reporting and the final Critic run again. A refresh also recovers a report stage that stopped on a final Critic call.

If an analysis finished `INCOMPLETE` only because a top finding has no contradiction search, use `--research`. It runs the missing research, reviews only the new evidence, and rebuilds the reports. The drafted sections are kept.

```bash
.venv/bin/python -m grit resume-report --research --pack ste --date 2026-11-05 --profile wilson --no-submit
```

Objections to the old report drafts are marked superseded, because each new report gets its own final critique. Add `--no-submit` to render without putting the packs into review.

For approved internal confirmations, add `--internal-evidence FILE` and `--evidence-approved-by PROFILE` to the reasoned weekly or agents command. `FILE` is a JSON list. Each record has `title`, `text`, `reference`, and an optional `published_at` date. A record can also state the business's own exposure:

```json
{"title": "Site register", "text": "STE A operates a depot at the airport.", "reference": "Site register 2026",
 "exposure": {"type": "SITE", "status": "CONFIRMED", "entity": "STE A"}}
```

`type` is `SITE`, `STAFF`, `CONTRACT`, `TRAVEL`, `ASSET`, or `OTHER`. `status` is `CONFIRMED`, `POSSIBLE`, or `NOT_EXPOSED`. Only such a record lets Assessment or the Critic call exposure confirmed or ruled out. A claim without it is recorded as possible exposure. The admitting profile must be a reviewer or a business-area head. Prepare the file with [Prepare the weekly exposure snapshot](docs/templates/exposure-snapshot-prompt.md). Without one, each report says that exposure rests on public sources only.

Keyword matching proposes each signal's risk links. In a reasoned run, Assessment and the Critic may remove a link the fact does not support. They may add a link the keywords missed, only from the pack's register of standing items. The report shows the reviewed links. The cascade to business-area and Group risks follows them.

A weekly report's status is `RED` only after a Risk & Assurance operator confirms the impact. A RED alert candidate from a headline pattern still drafts an alert. Until that confirmation, the weekly status is `ELEVATED`, `WATCHPOINT`, or `NORMAL`, as set out in [spec 45](specs/45_Weekly_Agent_Reasoning_and_Critique.md). A finding that Assessment marks as confirmed impact, with an internal record behind it, is `ELEVATED` until the operator decides.

After a named package decision, the CLI invokes Memory for typed proposals. Retry a failed Memory call with `grit weekly-memory --package PACKAGE --version V`. Proposals do not alter facts or appetite by themselves. A named reviewer can admit a shared correction with `grit memory-admit --id ID --profile PROFILE --intent ADMIT`. Only the affected reader can admit a reader preference. Admissions do not rewrite an approved report.

The reasoned path stays opt-in until an authorized, budgeted provider comparison and a review of the generated reports.

## Confirm a RED impact

List the candidates, then record a decision with your name and the impact. `GRIT_RED_CONFIRMERS` in `.env` names who may decide, separated by commas. The command refuses anyone else.

```bash
.venv/bin/python -m grit red list --pack urs --run 2026-11-02-urs
.venv/bin/python -m grit red triggers --pack urs
.venv/bin/python -m grit red confirm --pack urs --signal SIGNAL_ID --by "Jony Ling" --impact "IMPACT" --trigger TRIGGER
.venv/bin/python -m grit red reject --pack urs --signal SIGNAL_ID --by "Jony Ling" --impact "REASON"
```

Replace the placeholders:

- `SIGNAL_ID` is an ID from the first column of `grit red list`, such as `SIG-bdf6ea260c06752b`.
- `TRIGGER` is the RED trigger that happened to the business, from `grit red triggers`, such as `STAFF_HARMED` or `PROJECT_SUSPENDED`. A rule match is only a candidate.
- `IMPACT` is the confirmed effect, for example "Riyadh office closed for two days."
- `REASON` is why the impact is not confirmed, for example "No STE site or staff in the area."

Some patterns raise a candidate only with a link to the business. An AI or quantum cyber attack must involve an STE entity, customer, or supplier. A Middle East evacuation or a magnitude 8 earthquake must name a country where the pack has entities. An official (T1) or HIGH-rated (T2) outlet counts as corroboration.

Decisions are kept per pack and signal. The latest one stands. A report written before a decision stays as it was. Reissue the run's reports without repeating the review:

```bash
.venv/bin/python -m grit resume-report --refresh --pack urs --date 2026-11-02 --no-submit
```

The refresh needs a finished reasoned run for that date in the same database. A run made with `GRIT_DB` pointing at another file is not visible to it.

A RED candidate that the review excluded is omitted from the report. The status basis gives the number of excluded candidates. `grit red list` still shows them.

## Run the Monday schedule

`grit scheduled` runs the packs due that Monday, then the adverse news scan. The cadence and the planned query counts live in `grit/schedule.py`. `tests/test_schedule.py` locks these planned counts:

| Pack | Due | Planned Brave queries a run | Planned queries a month |
|---|---|---|---|
| `dsc` | Every Monday | 124 | 496 to 620 |
| `urs` | First and third Monday | 117 | 234 |
| `urs-tw` | First Monday | 60 | 60 |
| `ste` | First Monday | 48 | 48 |

The schedule test adds one 16-query source sweep for the month. A five-Monday month of these four packs is 978 queries, at or under 1,000 before the adverse scan. A live `grit weekly --dry-run` prints the queries for that date. A live run can send a different number when named customers and suppliers are searched. `SCHEDULE` names `dsc`, `urs`, `urs-tw`, and `ste`.

To see the packs due on a day, or to run them:

```bash
.venv/bin/python -m grit scheduled --dry-run --date 2026-11-02
.venv/bin/python -m grit scheduled
```

This crontab line runs the due scans at 06:30 each Monday. Replace the path with this repository.

```text
30 6 * * 1 cd /path/to/grit-scan-platform && .venv/bin/grit scheduled >> output/scheduled.log 2>&1
```

To change a cadence, edit `SCHEDULE` in `grit/schedule.py`. After the due scans, `grit scheduled` also runs the adverse news scan every Monday. Add `--no-adverse` to leave it out.

## Run the adverse news scan

The Adverse News & Lessons Learnt Scan is Wilson's weekly early-warning report on ST Engineering, its subsidiaries, key customers, suppliers, and competitors ([spec 46](specs/46_Adverse_News_and_Lessons_Learnt_Scan.md)). It follows the prompt in [Adverse_News_Lessons_Learnt_Scan_Prompt.docx](docs/Adverse_News_Lessons_Learnt_Scan_Prompt.docx). The sections are an Immediate Escalation Summary, adverse news relevant to STE, competitor and industry lessons learnt, emerging risk signals, a Management Attention Summary, and the Final Validation. The output is an email for you to review and send. The scan sends nothing.

The scan reads pool pages in the reporting period and adds searches from `config/adverse/Adverse-Scan-Plan.json`. It binds pages through the Group pack's graph and `config/adverse/Adverse-Watchlist.json`. A Screener, an Assessor, and a Critic propose. Rules in `grit/adverse/checks.py` check each fact against a verbatim quote from a page that was opened.

> **Warning:** A run sends up to 40 Brave queries (`--limit-queries`). With the Monday business-area scans, a month can exceed Brave's free credit of about 1,000 queries. Raise `BRAVE_MONTHLY_LIMIT` to match your Brave plan, or run with `--no-search` to use the pool only.

1. Print the reporting period, the planned queries, and the candidate counts. This sends nothing and calls no model.

   ```bash
   .venv/bin/python -m grit adverse --date DATE --dry-run
   ```

2. Run the scan. It prints the email at the end.

   ```bash
   .venv/bin/python -m grit adverse --date DATE
   ```

3. Open `output/adverse/DATE/review.md`. Check the withheld items, the rule decisions, and the Critic's objections.

4. Copy `output/adverse/DATE/email.txt` into a new email, or open `email.eml` in Outlook as a draft, and send it to Wilson.

The reporting period runs from the end of the previous `READY` scan to `DATE`. The first scan covers seven days. If a model call fails, the scan is `INCOMPLETE`. The email is written as `email-DRAFT-INCOMPLETE.txt`. The next scan's period still starts from the last `READY` scan.

## Run the daily lane

Once a day, GRIT-Scan reads the primary-source feeds that readers named. It asks GDELT's artlist for each standing condition and active management request. That call is capped, free, and not a Brave query. It sends one Currents query for the same set. The scan's own rules assess every page. Kept pages are queued for the next weekly or monthly scan. Once a week it also sweeps named sources that block feed readers, with one Brave `site:` query each, about 15 a week. A nightly index groups pages into events and writes a digest per pack for the Co-Pilot's "what's new" ([spec 42](specs/42_Daily_Lane_and_Nightly_Index.md)).

GDELT's seen time is the page date, because the artlist has no publication date. `--no-gdelt` skips GDELT. Sphera waits for an API contract. `secrets/sphera.env` is the placeholder for that license.

A new RED draft from the daily run is sent to the R&A duty team when `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`, `GRAPH_MAIL_SENDER`, and `GRIT_RED_MAIL_TO` are set. The app registration needs application permission Mail.Send. The message goes only to those mailboxes. A named person forwards it. If any setting is missing, the draft stays unsent.

URS Taiwan has its own daily run from official sources: USGS earthquakes, Taiwan NCDR disaster alerts, Taiwan MND's daily PLA activity reports, and NASA FIRMS fire detections. Request a FIRMS key and set `FIRMS_MAP_KEY` in `.env`.

Each daily run writes kept signals to `output/runs/DATE-PACK-daily/DATE-PACK-daily-signals.csv`.

1. List the feed register.

   ```bash
   .venv/bin/python -m grit feeds
   ```

2. Run the lane and the index. Add `--lookback-days 7` to catch up after a gap.

   ```bash
   .venv/bin/python -m grit daily
   ```

3. Optional: schedule it at 05:30 each day. Replace the path with this repository.

   ```text
   30 5 * * * cd /path/to/grit-scan-platform && .venv/bin/grit daily >> output/daily.log 2>&1
   ```

## Review a package

Each reader's pack is a package in the workflow. Every member signal runs from `NEW` to `PENDING_REVIEW` through `TransitionGuard()`, in an execution of its own for that reader.

1. Decide on a package. `ReviewGate()` decides. The Supervisor writes the ledger. Intents are `APPROVE`, `REQUEST_REWORK`, `HOLD`, and `REJECT`. A reader-only profile is refused.

   ```bash
   .venv/bin/python -m grit review --package PKG-DATE-urs-tw-gareth --version 1 --profile gareth --intent APPROVE
   ```

2. Export to AIRIS. When the business-area head approves a package, its tier 1–2 members that are linked to a register move to `AIRIS_HANDOFF_READY`. A signal goes once. The export is JSON and Excel in `output/airis/`. Each row names the package version and hash.

   ```bash
   .venv/bin/python -m grit airis --dry-run
   .venv/bin/python -m grit airis
   ```

A changed brief cannot replace a package that is still pending. Return the pending version for rework, then write the pack again. The new version gets its own folder.

## Open the dashboard

Start the dashboard, then open the URL it prints. The default port is 8501. From an editor on a remote host, forward the port first.

```bash
.venv/bin/python -m grit ui
```

Pick a reader and a run in the sidebar.

- **Dashboard:** the bottom line, the conditions, named items, watchpoints, and the QA log.
- **Signals:** every signal in the reader's view, with why it is there. Approve, reject, and watch appear only when the reader's profile turns them on.
- **Packs and review:** download the files, write this week's pack, and decide on a pending package. **Approve** calls `ReviewGate()` with intent `APPROVE`. A reviewer or a business-area head sees that button.
- **Management requests:** propose a concern, topic, trend, or keyword. A reviewer activates it. An active request adds queries to the next scan and assigns P1 to matching signals.
- **RED and AIRIS:** RED drafts to send or dismiss, and the business-area head's AIRIS export.

The Co-Pilot sits in a drawer you can open, close, and resize ([spec 40](specs/40_Copilot_Core.md)). It reads the tab you have open. It also answers from briefs, signals, the report library, and the registers in the reader's reach. Wilson, the CEO, and the Chairman search every pack. Other readers search their own. Each answer gives the answer, the evidence, what it means for STE, and what is uncertain. Every claim is cited. An answer that fails its checks is withheld. Each reader's conversation is private to them.

When the records are silent or older than the question needs, the Co-Pilot searches the web: Currents first, Brave when Currents finds nothing, 17 searches a day for each reader ([spec 41](specs/41_Profiler_Fit_Report_and_Web_Search.md)). Web results are marked unvetted. **Add to pool** under a web citation queues the page for the next scan.

- Select **What's new since yesterday?** for the daily digests.
- Choose a standing condition to focus the Co-Pilot on it.
- Type `/concern TITLE: keyword, keyword` (or `/topic`, `/trend`, `/keyword`) to propose a management request. A page is kept for a request only when its headline or lead names a keyword exactly. A reviewer can correct keywords with `grit requests revise --id ID --keywords "..." --reason "..."`.
- Type `/note TEXT` to leave a note. It goes to `secrets/readers/<profile>/inbox/`.

To ask from the command line as a named reader:

```bash
.venv/bin/python -m grit ask --profile wilson "What is driving the risk to our GPU and HBM access?"
```

## Reader packs

A reader pack is a folder under `config/` with a `pack.json` manifest. The manifest names the pack's level, its parent and children, and its register files. Counts below are the exposure registers and theme files as of 8 November 2026. Rebuild a pack after the workbook changes, then read `grit pack-show`.

| Pack | Level | Standing items | Named items in the exposure register |
|---|---|---|---|
| `urs` | Business area | 14 themes | 41 entities, 33 customers, 19 suppliers. 201 competitors sit in the competitor register and rotate. |
| `urs-tw` | Entity, under `urs` | 10 standing conditions | 11 projects, 2 customers, 9 suppliers, 2 sites |
| `dsc` | Business area | 16 themes | 21 entities, 69 customers, 39 suppliers, 26 competitors |
| `ca` | Business area | 16 themes | 57 entities, 458 customers, 45 suppliers, 43 competitors. The scan plan is still proposed for CA review. |
| `marine` | Business area | 16 governed themes. 7 draft themes are listed for review. The scan uses the governed themes. | 11 entities, 27 customers, 14 suppliers, 50 competitors. The scan plan is still proposed for Marine review. |
| `ste` | Group | 13 standing conditions. The baseline is a draft pending Wilson's sign-off. | Footprint comes from `dsc`, `urs`, `ca`, and `marine`. |

A business-area brief summarizes entity packs that ran on the same date. Run entity packs first. `grit weekly` orders packs that way when more than one pack is selected.

Several scan plans are still drafts. The `status` field on each scan plan names who reviews it.

## Prepare the Group pack

The Group pack (`ste`) briefs the CEO and the Chairman each month on the geopolitical and technology picture facing ST Engineering ([spec 37](specs/37_STE_Group_Pack.md)). It binds news through its business-area children. For each condition with evidence, the Assessor writes the risk pathway, any opportunity, and the counter-indicator. The Proposer writes options.

1. Write the pack and the two reader profiles. Existing files are kept.

   ```bash
   .venv/bin/python -m grit pack-init --pack ste
   ```

2. Add `"ste"` to `packs` in `config/profiles/wilson.json`. Add an `ste` brief: copy `briefs.ste` from `config/profiles/vschong.json` and set `preparedFor`. Wilson's profile in this checkout already lists `ste`.

3. Run the month. A monthly pack runs only when you name it.

   ```bash
   .venv/bin/python -m grit weekly --pack ste
   ```

4. Draft the baseline for Wilson's sign-off. The draft goes to `output/runs/DATE-ste/baseline-draft/`. After he signs off, copy each `currentAssessment` into `config/ste/STE-Global-Baseline.json` and set its `status` to `SIGNED_OFF`. The file in this checkout is still `DRAFT_PENDING_SIGN_OFF`.

   ```bash
   .venv/bin/python -m grit baseline-draft --pack ste --date DATE
   ```

5. Each quarter, propose the best, medium, and worst case for STE under each strategic shift ([spec 43](specs/43_Best_Medium_Worst_Scenarios.md)). Wilson edits them and saves them as `config/ste/STE-Scenarios.json`. Add `--redraft` to redo only the shifts whose draft was withheld.

   ```bash
   .venv/bin/python -m grit scenarios --pack ste --date DATE
   ```

6. Record Wilson's approval. The draft becomes the scenario register, with keywords for every signpost.

   ```bash
   .venv/bin/python -m grit scenarios-approve --pack ste --date DATE --by "Wilson Yeo"
   .venv/bin/python -m grit scenarios-track --pack ste --date DATE
   ```

   The monthly radar then shows which case each shift's signposts lean toward, the signposts seen, and the planned responses they trigger. The lean is a count of signposts seen. It is not a probability. A case can also carry one sentence on where STE could gain, marked "Hypothesis: to validate with BAs". The tracker counts five leading indicators. Each has a green, amber, or red band on bounds GRIT-Scan proposes. The band stays proposed until Wilson confirms it. PLA activity reads the sortie counts in Taiwan MND's daily reports.

The monthly brief is the Group Strategic Radar. Wilson reviews it before it is released.

## Draft a State of the Situation

A State of the Situation (SotS) document explains one strategic topic in depth. It is built from the thought-leadership reports in the library. Every sentence cites a report page ([spec 38](specs/38_Report_Library_and_State_of_the_Situation.md)). It is the base for the Group pack's forecasts and options. There are 12 topics: Wilson's strategic shifts, plus China-Taiwan, defence budgets, competitors, and interest rates, FX, and inflation.

1. Put the reports in `secrets/reports/`. `grit/library/catalogue.py` names each file, its publisher, and its edition.

2. Read and embed them. A report that has not changed is skipped.

   ```bash
   .venv/bin/python -m grit library-ingest
   ```

3. Draft the four pilot topics: the Middle East, Ukraine, semiconductors and GPUs, and quantum-safe migration. Use `--topic` for others, or `--all`.

   ```bash
   .venv/bin/python -m grit sots
   ```

The drafts go to `output/sots/DATE/` as Word, Markdown, and JSON. To draft one section again, use `--redraft SECTION --from DATE`. To render again without a model call, use `--render-only DATE`. Wilson edits and signs off each document.

## Reader profiles

Profiles are JSON files in `config/profiles/`. A profile changes what a reader sees and in what order. Shared facts stay as the pool recorded them.

| Profile | Role | Aperture | Review role | Brief |
|---|---|---|---|---|
| `wilson` | Group Head, Risk & Assurance | Wide | Reviewer | URS, URS Taiwan, DS&C, Commercial Aerospace, Marine, and the Group radar. |
| `hwaimay` | R&A business partner, URS | Material | Reviewer | URS and URS Taiwan. |
| `gareth` | Head, URS | Material | BA head | URS and URS Taiwan. Approves URS for AIRIS. |
| `ksyong` | Head, URS Taiwan | Material | Reader | URS Taiwan only. |
| `yikai` | R&A business partner, DS&C | Material | Reviewer | DS&C. |
| `jplow` | President, Digital Systems | Material | BA head | DS&C. |
| `ecgoh` | President, Cybersecurity | Material | BA head | DS&C. |
| `group-cfo` | Group CFO | Wide | Reader | RED alerts for URS, URS Taiwan, and DS&C. No weekly brief. |
| `vschong` | Chief Executive Officer | Material | Reader | The monthly Group Strategic Radar, after Wilson approves it. |
| `mkteo` | Chairman | Material | Reader | The monthly Group Strategic Radar. |

A profile lists its packs in `packs` and one brief per pack under `briefs`. Add `RESPONSE_OPTIONS` to a brief's `sections` to show the Proposer's options under each key risk.

A wide-aperture reader sees every kept signal. A material-aperture reader sees a signal only with P1, P2, or P4 priority, or a standing-condition match ([spec 24](specs/24_Reader_Profile_and_Aperture_Routing_Specification.md)). News priority is P1 for an active management request, P2 for a direct exposure path, P3 for a competitor, sector, footprint, or theme match, and P4 for a customer ([spec 29](specs/29_News_Priority_and_Management_Request_Model.md)).

A reader's preferences come from what they say ([spec 39](specs/39_Reader_Notes_and_Profiles.md)). Save the note, write `profile.md` with every line citing a note, and check it. Notes and profiles live in `secrets/readers/`, which git ignores.

```bash
.venv/bin/python -m grit notes add --profile wilson --file PATH --source email --date 2026-10-09 --move
.venv/bin/python -m grit profile check --profile wilson
.venv/bin/python -m grit profile propose --profile wilson
.venv/bin/python -m grit profile accept --profile wilson
```

`PATH` is the saved note. The proposal and a review file go to `secrets/readers/<profile>/proposals/`. After each brief, `grit report` writes a fit report to `secrets/readers/<profile>/fit/`. Emphasis moves a signal within its tier by at most six places. Every kept signal remains in the brief. A P1 signal and a RED signal keep their place or move up. A profile with a problem stops the brief and the dashboard from loading, and the error names the file.

## Registers

| Register | What it holds |
|---|---|
| `config/urs/` | Urban Solutions, built from the workbook. |
| `config/urs-tw/` | URS Taiwan: ten standing conditions, named items, sources, the scan plan, and order-book anchors. |
| `config/dsc/` | Digital Systems & Cyber, built from the workbook. |
| `config/ca/` | Commercial Aerospace, built from the workbook. The scan plan and line-of-business hierarchy are proposed until CA reviews them. |
| `config/marine/` | Marine, built from the workbook. Class-of-counterparty rows stay in the register and are not searched by name. |
| `config/ste/` | The Group pack: themes, baseline, scan plan, and scenarios. |
| `config/adverse/` | The adverse news watchlist, lexicon, and scan plan. |
| `config/risk/` | The Group risk universe and `Risk-Cascade.json`. Every crosswalk row is proposed until R&A approves it. |
| `config/red-patterns.json` | RED patterns and the RED triggers that R&A confirms. |
| `config/source-ratings.json` | Shared outlet credibility ratings and the contested-outlet list. As of 8 November 2026: 82 ratings and 4 contested outlets. |
| `config/profiles/` | Reader profiles. |
| `config/registry_aliases.json` | Shared alias overrides. |

**Rate sources.** Each rating is `PROPOSED` until a named person confirms it.

```bash
.venv/bin/python -m grit ratings list
.venv/bin/python -m grit ratings set reuters.com HIGH --by "Wilson Yeo" --name Reuters
.venv/bin/python -m grit ratings confirm --by "Wilson Yeo"
.venv/bin/python -m grit ratings contest naturalnews.com --by "Wilson Yeo" --reason "Publishes unverified claims."
.venv/bin/python -m grit ratings unrated --run 2026-11-02-urs
```

`grit ratings unrated` lists a run's outlets with no band, most-used first. A new rating applies from the next `grit assess`, `grit agents`, or `grit weekly`.

Every source gets a band by rule: T1 official, T2 rated HIGH, T3 rated MEDIUM or LOW, `UNRATED`, T4 aggregator, or `CONTESTED`. A fact that cites a contested outlet needs a second outlet. A finding that rests only on unrated or T4 sources is watched. Each report's quality check names the cited outlets that still need a band.

**Measure a report.** `grit scorecard` scores each report of a run against the URS methodology: source bands, escalating rows, cut sentences, passing mentions, trend words, contradiction search, and retired phrases.

```bash
.venv/bin/python -m grit scorecard --pack urs --date 2026-11-02
```

**Carry corrections forward.** Record a correction once. Every later report of the pack lists it until you close it. `--phrase` retires exact wording, such as `S$600m`.

```bash
.venv/bin/python -m grit corrections add --pack urs --earlier "Taiwan opportunity loss S\$600m" \
  --why "Item 5 final v2 revised the figure" --corrected "S\$645m opportunity loss" \
  --source "Item 5 final v2" --update-old "Taiwan scans before 29 Oct" --phrase "S\$600m" --by "Jony Ling"
.venv/bin/python -m grit corrections list --pack urs
.venv/bin/python -m grit corrections close --pack urs --id COR-urs-001 --by "Jony Ling" --reason "Every deck now shows S\$645m."
```

To show a new correction in a finished run's reports, run `grit resume-report --refresh` for that date.

**Management requests.** A numbered request, and a focus item marked "Management priority" or "Core control focus", start `ACTIVE`. A focus item marked "Research draft" starts `PROPOSED`. After that, only a reviewer changes a request's status. An active request's keywords make a matching signal P1. A keyword `A + B` needs every part in the title or lead.

## Export relationship gaps

The workbook states too few relationships for entity-level reasoning. Export the questions for the business area, then import the answers into that pack's relationships file. The CSV files hold names from the confidential workbook. Keep those CSV files out of git.

```bash
.venv/bin/python -m grit gaps export
.venv/bin/python -m grit gaps import --relationships output/ba-review/relationships-to-fill.csv --aliases output/ba-review/aliases-to-review.csv --dry-run
```

## Run the business-area digest

The earlier digest covers the `urs` ontology on a 12-week query cycle and writes a Markdown digest with finding cards. It works on run folders under `output/brave-plan/`, not the pool. Its commands are `grit digest-plan`, `digest-search`, `digest-embed`, `digest-filter`, `digest-loop`, and `digest-report`. Each takes `--help`. To fold due digest queries into a weekly run, add `--sweep N` to `grit scan` or `grit weekly`.

## Deterministic rules

These rules decide what a brief may say. The module is the full statement.

| Rule | Module | What it decides |
|---|---|---|
| Link ladder | `grit/ontology/link.py` | One rung per page, strongest first: `DIRECT`, `COUNTERPARTY`, `COMPETITOR`, `PERIMETER`, `SECTOR`, `THEME`. A name counts in the title, in the lead, or when it is named twice. |
| Reader index | `grit/ontology/reader_index.py` | Which named items a page hits. A longer capitalized name is a different name. A supplier, customer, or site needs one of the pack's context cues. |
| Public-body names | `grit/pipeline/reader_scan.py` | A ministry, authority, agency, board, or similar body counts only when the page does not place it in another country and uses the business area's own vocabulary. The mention stays recorded. The rung falls back. |
| Keep rule | `grit/core/reader_filter.py` | A weekly run covers the run date and the six days before it. A page published after the run date is dropped. The rule keeps an event page that links to the register, names a reader-pack item, bears on a condition or hazard, or hits a KEY theme. Near-duplicate pages merge. Reports of an official daily series stay separate by day. |
| Publisher family | `grit/core/publisher_family.py` | Corroboration counts publisher families. Two titles in one family count once. An aggregator counts only when nothing else carries the story. |
| Briefing priority | `grit/core/priority.py` | Tier 1 is big and direct. Tier 2 is big or direct and significant. Tier 3 is linked. Tier 4 has no register link. |
| Confidence | `grit/core/confidence.py` | `HIGH` needs two independent publishers and one rated `HIGH`. `MEDIUM` needs two publishers or one `HIGH`-rated publisher. Otherwise the label is `LOW`. With nothing cited, the brief says "not established". |
| RED | `grit/core/red_alert.py` | A pattern must hit the title or lead, in a sentence that reports an event, and the event must be corroborated or carried by a `HIGH`-rated source. A forecast, an assessment, or a drill stays off the list. |
| Harm | `grit/core/impact_scoring.py` | Evidence, horizon, severity, transmission, a verdict, and opportunity cost. Severity is the maximum across dimensions. Financial severity uses value-at-risk only. |

An outlet's rating comes from the pack's own source register first, then from `config/source-ratings.json`. Edit the shared list with `grit ratings`.

## Signal pool

The pool is one SQLite file, `output/grit.sqlite`. Set `GRIT_DB` to move it. The schema is [grit/pool/schema.sql](grit/pool/schema.sql). Shared facts (`signals`, `sightings`, `vectors`) stay apart from reader-scoped records (`assessments`, `feedback`, `packages`). `workflow_states` is append-only. Every agent and logged function call is in `execution_log` with its prompt version and hashes.

To print the row counts, run `.venv/bin/python -m grit db`.

Dataverse remains the system of record. This command prints the push. It writes to Dataverse only when you add `--push`. Vectors stay in the pool, because `grit_signals` has no vector column.

```bash
.venv/bin/python -m grit dataverse --date DATE
```

## Command reference

Run `grit COMMAND --help` for a command's arguments. Delegated commands take their own flags after the command name.

| Command | What it does |
|---|---|
| `plan`, `scan`, `assess`, `agents`, `report`, `weekly` | One stage, or the whole week. |
| `resume-report` | Recover or refresh a reasoned report stage. |
| `scheduled` | Run the Monday packs, then the adverse scan. |
| `adverse` | Write the adverse news email for review. |
| `daily`, `feeds`, `index` | The daily lane, the feed register, and the nightly index. |
| `red` | List RED candidates, or record a confirmation or rejection. |
| `review`, `airis`, `feedback` | Decide a package, export to AIRIS, or record a signal decision. |
| `requests`, `corrections`, `misses`, `ratings`, `scorecard` | Requests, the corrections ledger, review misses, source bands, and the methodology score. |
| `scenarios`, `scenarios-approve`, `scenarios-track`, `baseline-draft` | Cases, approval, the signpost tracker, and the baseline draft. |
| `pack-build`, `pack-show`, `pack-init`, `gaps` | Build, print, seed, or fill a pack. |
| `library-ingest`, `sots` | Read the report library, or draft a State of the Situation. |
| `notes`, `profile` | File a reader note, or check, propose, and accept a profile. |
| `ask`, `ui`, `pool-add` | Ask the Co-Pilot, open the dashboard, or queue a web page. |
| `db`, `dataverse` | Print pool counts, or push kept signals. |
| `weekly-memory`, `memory-admit` | Retry Memory proposals, or admit one. |
| `digest-plan`, `digest-search`, `digest-embed`, `digest-filter`, `digest-loop`, `digest-report` | The 12-week business-area digest. |

## Known gaps

These are the open items for the MVP.

1. GDELT's artlist feeds the daily lane. The weekly scan uses Brave, with Currents as the fallback. Sphera stays closed until an API contract exists. `secrets/sphera.env` is the placeholder.
2. Severity stays "not established". No source supplies structured observations. No value-at-risk register exists.
3. Shared outlet bands live in `config/source-ratings.json`. A pack source with no band stays `UNRATED` until you rate it. `grit ratings unrated` lists the outlets a run actually cited.
4. The URS Taiwan order-book anchors are `REPORTED_FOR_CONFIRMATION`. A named business-area owner confirms them.
5. The business-area relationship files have no edges yet. Entity-level paths stay limited until those rows are imported. See [Export relationship gaps](#export-relationship-gaps).
6. Scheduled scans start from cron and `grit scheduled`. A daily RED alert is mailed when Graph mail is configured. Otherwise it stays a draft. The dashboard has no user sign-in. The workflow ledger is written to the pool. Dataverse receives signals only through `grit dataverse --push`.
7. Commercial Aerospace and Marine have scan plans, and `grit weekly` with no `--pack` includes them. `SCHEDULE` does not. Use `grit scheduled`, or name `--pack`, when you want the Monday budget.
8. A generated short name can match the wrong company. On 7 October 2026, "Amtech Systems" matched a US semiconductor company. Reject such aliases through `grit gaps`.

## Specifications

Read [specs/README.md](specs/README.md) before you change behavior a spec names. [Spec 35](specs/35_GRIT_Scan_POC_Architecture.md) describes the original proof of concept and lists what the MVP must specify.
