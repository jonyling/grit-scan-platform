# GRIT-Scan improvement plan, 26 October 2026

This page lists the 30 points of improvement Jony Ling raised on 26 October 2026 and the batches that deliver them. Each point has its current status. Use the page to choose the next batch, and to hand the work to another coding agent.

Status as of 28 October 2026: Batches A, B, C, D, and E are done. Batches F and G are not started.

## Rules for the next agent

Every agent working on this repository must follow these rules:

- Do not read or print anything in `config/`, `output/` or `.env`. Organisation content policy blocks them. Code can write to `config/` without reading it, as scenario approval does.
- Do not stage `config/`, `output/` or `secrets/` in a commit.
- Agents propose, deterministic checks decide, and named humans approve. A fact that is missing stays absent. Do not invent it.
- Run the tests before each commit. They make no network call.

  ```bash
  .venv/bin/python -m pytest -q
  ```

  As of 29 October 2026, 614 tests pass. The URS and DS&C workbooks in `secrets/` rebuild the same registers that are already in `config/`.

- End each commit message with this trailer:

  ```text
  Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
  ```

- Update `README.md` and the matching spec in `specs/` when behaviour changes.
- Keep Brave Search use within its free credit of about 1,000 queries a month. The `grit scheduled` timetable already uses about 978 in its busiest month. Brave's credit renews on 1 November.

## Points of improvement

| # | Point | Batch | Status |
|---|---|---|---|
| 1 | Add more URS and DS&C entity information to the ontology, and update it before upload to Google and each pack run. | F | Not started |
| 2 | Get entity information from the six BAs that have not sent it: Commercial Aerospace, Satcom, Defence Aerospace, Marine, Land and Homeland. | None | Organisational. Needs higher authority. |
| 3 | Add RED events: pandemic, China attacks Taiwan, earthquake above 8.0, financial meltdown, major corruption cases, workplace accidents, Middle East attacks needing evacuation, AI and quantum cyber attacks, sanctions on linked entities, bankruptcy of key customers, suppliers or projects, trade barriers and tariffs, sudden loss of key personnel, EHS incidents causing the death of STE staff, labour incidents and strikes, disruption of basic services, legal disputes affecting STE entities, and major riots where STE entities or projects are. | B | Done. Patterns are in `grit/core/red_events.py` and merge at assessment. Magnitude 8.0 or greater. Linked events need a direct or counterparty link. `RED-PANDEMIC` was already in the register and was left as written. `grit daily`, `grit scan`, and `grit assess` append missing ids to `config/red-patterns.json` without replacing an existing rule. That file is not committed. |
| 4 | Email RED alerts from daily scans to the relevant people at once, for example the PHP vulnerabilities. | B | Built. A daily draft is sent to `GRIT_RED_MAIL_TO` when Graph mail is configured. No address is invented. Until those settings exist, the draft stays unsent. Wilson still chooses who is on the duty list. |
| 5 | Add GDELT and Sphera acquisition lanes. | B | GDELT artlist is on the daily lane, with a timeout and the keep rule. Sphera stays closed: `secrets/sphera.env` is the placeholder, and nothing is fetched until an API contract exists. |
| 6 | Make table rows tall enough to show all text. Colour trend labels: Escalating red, Easing green, Unchanged neutral. | C | Done. Short columns stay on one line. A long basis, title, or keyword list wraps on spaces, and a wide table scrolls sideways. Escalating is red, Easing is green, and Unchanged is neutral. The word stays the label. |
| 7 | Let the reader open and close the Co-Pilot panel, as in Microsoft 365 Copilot, and resize its width. | C | Done. Show Co-Pilot and a width control are in the sidebar. A closed panel leaves the dashboard full width. |
| 8 | Make the Co-Pilot read the tab the reader has open, such as Signals, and not always the Dashboard. | C | Done. The open tab and its records are the Co-Pilot context. Dashboard focus is sent only on the Dashboard tab. |
| 9 | Write a CSV of signals for each daily pack. | B | Done, commit `fa9e79b` |
| 10 | In `docs/design/schema.yaml`, list BA right after STE, then LOB. | A | Done, commit `7140799` |
| 11 | Add an Approve button for packages in the UI. | C | Done. Approve calls ReviewGate with intent APPROVE. A reader is refused. |
| 12 | Keep a weekly run strictly to its 7 days ending on the run date. | A | Done, commit `7140799` |
| 13 | Count corroboration by publisher family, not by number of sources. | A | Done, commit `7140799` |
| 14 | Add official hazard and cross-strait sources for URS Taiwan: USGS, NASA FIRMS, Taiwan MND and the Taiwan weather service. | B | Done, commit `fa9e79b`. FIRMS needs a key. |
| 15 | State in each report and deck that the content is GRIT-Scan AI-generated suggestions for consideration, with a disclaimer on AI limits and the need for human review. | D | Done. The statement is on the cover and in the footer of every Word brief, Markdown preview, and slide. |
| 16 | Improve executive summary slide 2: remove truncation marks ("…") and say what each point means for DS&C. | D | Done. Each summary card keeps whole sentences and says what the point means for the pack's short label. |
| 17 | Move to a newer, more capable Gemini model, such as Gemini 4 Argon. | G | Not started |
| 18 | Put a timeout on every call to an outside server. | A | Done, commit `7140799` |
| 19 | In State of the Situation (SotS) and Group packs, add for each scenario a hypothesis on where STE could gain, marked for BAs to validate. | E | Done. The line is copied only when a reviewed case already states it, and it is marked "Hypothesis: to validate with BAs". |
| 20 | In SotS, give an exposure table for every BA: Taiwan and China entities, staff and key suppliers, chip or component dependence, revenue and asset concentration, and a High, Medium or Low rating per scenario. Mark each as BA-confirmed or proposed. | E | Done. A filled cell is proposed until the business area confirms it. A fact the register does not hold stays blank. |
| 21 | Turn indicators into leading signals with green, amber and red thresholds: PLA activity, shipping and insurance, export controls and sanctions, cross-strait politics, and overseas fab ramp-up. | E | Done; fixed 29 Oct. Each indicator has proposed green, amber and red bounds, printed as "proposed bounds, for Wilson to confirm" until he confirms them (`BOUNDS_STATUS` in `grit/pipeline/indicators.py`). PLA activity reads the most PLA aircraft sorties in one day from Taiwan MND's daily reports: green up to 20, amber up to 45, red above. The others count kept signals in the window. A week with no reading has no band. Red names what the BAs do. |
| 22 | Do not repeat the same figures or points within one report. | D | Done; fixed 29 Oct. A later sentence is dropped when it repeats an earlier sentence, or when it restates an earlier point: every figure in it was used before, in one sentence that shares at least half of its content words. A sentence that only shares a figure with an unrelated sentence is a different fact and stays. Appendix slides keep their full text, because the summary links to them. A table cell stays as written. The disclaimer can repeat. |
| 23 | Make the SotS history section fuller, citing authoritative sources beyond the report library. | E | Done. History uses more passages from the topic's own reports. Sources beyond the library are listed as proposed and are not cited until a named person adds the file. |
| 24 | Map Group risks down to BA and LOB, and BA risks up to Group. | E | Done. Each Group risk lists the business-area themes that crosswalk up to it, and any LOB the register names. A blank LOB cell stays blank. The crosswalk stays proposed. |
| 25 | Recommend actions down to specific signals, scenarios (Best, Medium, Worst) and predictions. | D | Done. A finding or a triggered scenario response shows an action only when that action already exists, marked "Suggestion for the business area to consider." |
| 26 | Base profiles on each BA's business partner, not the office holders. Business partners filter signals to office holders. | F | Blocked. Profiles are in `config/`. |
| 27 | Add Wilson's Group-level daily adverse news agent, and make its output deterministic. | F | Blocked. Needs Wilson's prompt. |
| 28 | Support four feedback routes: the UI's package review, management requests, Co-Pilot chat, and Jony through VS Code chat with uploaded documents. | F | Not started |
| 29 | Allow 2 or 3 summary slides when the period has many critical signals. | D | Done. One summary slide for up to two critical items, two slides for three or four, and three slides for five or more. |
| 30 | Fix Co-Pilot chat history that seems shared between profiles. | A | Done, commit `7140799` |

## Batch plan

Batches are ordered by value. Do one batch per session.

### Batch A: correctness (done)

Commit `7140799`.

- **Point 12.** The keep rule drops pages dated after the run date (`AFTER_RUN_DATE`), and a weekly run keeps its run date and the 6 days before it. Brave is asked for the run's own date range, not "past week". The 16 October URS and URS Taiwan runs had kept 245 and 229 pages from after their date. Rebuild them after Brave's credit renews on 1 November.
- **Point 13.** `grit/core/publisher_family.py` maps domains to owners. Press-release wires count once. Aggregators count only when nothing else carries the story. Corroboration counts can fall, so some items drop below the RED threshold.
- **Point 18.** Every HTTP and Gemini call already had a timeout. Microsoft sign-in (MSAL) now has a 60-second timeout.
- **Point 30.** Stored history was already per profile. The Co-Pilot widgets shared one key across readers. Every widget key now includes the profile, and the panel names whose conversation it shows.
- **Point 10.** BA follows STE in `docs/design/schema.yaml`.

### Batch B: alerts and daily lane (done)

Commit `fa9e79b` delivered points 9 and 14. Commit `cfddaf6` delivered points 3, 4, and 5.

- **Point 9 (done).** Each daily run writes `output/runs/DATE-PACK-daily/DATE-PACK-daily-signals.csv`, in the brief's order, UTF-8 with a byte-order mark for Excel.
- **Point 14 (done).** URS Taiwan has its own daily run, fed by:
  - USGS earthquakes of magnitude 5.0 or more in the Taiwan region;
  - Taiwan NCDR official alerts, which carry the Central Weather Administration's warnings;
  - Taiwan MND's daily PLA activity reports, with counts in the title;
  - NASA FIRMS fire detections, which need a free `FIRMS_MAP_KEY` in `.env`.

  The Central Weather Administration is registered as blocked, because its RSS terms forbid derived products. Reports from an official daily series are never merged with each other across days.
- **Point 3 (done).** The new events are in `grit/core/red_events.py`. Assessment merges them after the rules already in `config/red-patterns.json`. A rule id that is already there is not replaced, so an edited rule stays. `grit daily`, `grit scan`, and `grit assess` append any missing ids. The file is not committed. Each pattern has a test. An earthquake is magnitude 8.0 or greater. A sanction, bankruptcy, tariff, strike, corruption case, workplace accident, or loss of key personnel needs a direct or counterparty link. Death of STE staff, a legal dispute, and a riot need a direct link.
- **Point 4 (done as far as code can go).** A daily run sends each new RED draft through Microsoft Graph to the mailboxes in `GRIT_RED_MAIL_TO`, and to nobody else. The status becomes `SENT_TO_DUTY`. A named person forwards it. If the Graph settings or the duty list are missing, or an address is not a mailbox, nothing is sent. This still needs a mail-enabled app registration and Wilson's list of duty mailboxes.
- **Point 5 (done for GDELT).** The daily lane asks GDELT's artlist, with a timeout and the same keep rule. The page date is GDELT's seen time. Sphera is not called. `secrets/sphera.env` is created when missing, and a filled file still fetches nothing until an API contract exists.

### Batch C: user interface (done)

Commit `0381af6`.

- **Point 6.** Tables are HTML. Short columns stay on one line. A long basis, title, or keyword list wraps on spaces, and a wide table scrolls sideways. Escalating is red, Easing is green, and Unchanged is neutral. The word remains in the cell.
- **Point 7.** **Show Co-Pilot** and **Co-Pilot width** are in the sidebar. The width is 24 to 50 percent. A closed panel leaves the dashboard full width.
- **Point 8.** The Co-Pilot receives the open tab and up to 40 of its records. A condition chosen on the Dashboard is sent only while that tab is open.
- **Point 11.** **Approve** calls `ReviewGate()` with intent `APPROVE`. A reader sees a refusal instead of the button, and `review_package` raises `PermissionError` if a reader reaches it.

### Batch D: reports (done)

Commit `7f3573f`. Commit `6387e60` keeps short dashboard columns on one line.

- **Point 15.** Every Word brief, Markdown preview, and slide carries this statement on the cover and in the footer: "GRIT-Scan AI-generated suggestions for consideration. The scan can miss a source, misread a page, or leave out context. A named person reviews this before anyone acts on it."
- **Point 16.** Each summary card keeps whole sentences, with no truncation mark. The first sentence is kept whole when it is longer than the box, and the box wraps it. The card says what the point means for the pack, using the pack's short label.
- **Point 29.** A critical item is a key risk or an escalating theme, or, on a Group pack, a condition with evidence this period. One summary slide covers up to two items, two slides cover three or four, and three slides cover five or more. Each slide shows at most two full cards. Items past the sixth are named in full on the last slide. The Group scenario outlook stays after those slides.
- **Point 22.** Before the file is saved, a later sentence that repeats an earlier sentence, or whose figures were all used earlier, is dropped. A table cell is left as written. The disclaimer can repeat. A suggestion is dropped only when that same suggestion was already shown.
- **Point 25.** A finding lists a suggestion after its hypothesis when a Proposer option or a triggered scenario response already cites that signal. The line starts with "Suggestion for the business area to consider." A signal, a scenario case, or a hypothesis with no existing action gets none.

### Batch E: State of the Situation and scenarios (done)

Commit `24a4dc8`.

- **Point 19.** A reviewed case can carry `where_ste_could_gain`. The Group brief, the scenario draft, and the State of the Situation copy that sentence and mark it "Hypothesis: to validate with BAs". The Strategist leaves it empty when the sources do not support a gain. A certainty word withholds the draft.
- **Point 20.** The exposure section has one row per business area. Taiwan and China entities, staff, key suppliers, chip or component dependence, revenue and asset concentration, and a High, Medium, or Low rating for each case are filled only from the register. A filled cell is proposed until `exposureStatus` is `BA-confirmed`. A missing fact stays blank.
- **Point 21.** `grit/pipeline/indicators.py` defines the five leading indicators. The scenario tracker counts kept signals whose title or lead contains an indicator phrase. Green, amber, and red apply only when both bounds are set. Until a named person sets them, the band is not established.
- **Point 23.** The history section retrieves a second set of passages from the topic's own reports, up to 20. Under that section, the document lists the topic's proposed sources that are not in the library. A sentence cannot cite one until a named person adds the file.
- **Point 24.** `RiskCascade.links()` lists, for each Group risk, the business-area themes in the crosswalk and any LOB named on the row or the theme. The Group brief shows that table. A business-area theme still rolls up to Group through the existing proposed crosswalk. A LOB the register does not name stays blank.

### Batch F: data and governance

- **Point 1.** Refresh the URS and DS&C entity information in the ontology before upload and pack runs. The latest workbooks are in `secrets/`.
- **Point 26.** Re-base profiles on BA business partners. Needs `config/` access or Jony's edits.
- **Point 27.** Add Wilson's daily adverse news agent as a lane whose output passes the same deterministic checks. Needs Wilson's prompt.
- **Point 28.** Record all four feedback routes in one feedback table, with the route, the person and the date.

### Batch G: model

- **Point 17.** Check which newer Gemini models the Vertex AI project can use, then make the model a setting and test it on one pack before switching. The current model is `gemini-3.8-flash`. Gemini 4 Argon availability is not yet confirmed.

## Decisions and inputs needed

| Input | Needed for | From |
|---|---|---|
| Microsoft 365 mail app credentials and the R&A duty mailboxes (`GRIT_RED_MAIL_TO`) | Point 4, so mail leaves the draft | Jony, Wilson |
| Sphera licence and API access | Point 5 | R&A |
| Free NASA FIRMS key in `.env` | Point 14 | Jony |
| Business-partner profile edits | Point 26 | Jony |
| Wilson's daily adverse news prompt | Point 27 | Wilson |
| Entity information from six BAs | Point 2 | Wilson or higher authority |

## Credit projection

Projected on 26 October 2026, measured in working sessions of one batch each:

| Scope | Sessions |
|---|---|
| Essentials: batches A to D | About 4.5 to 5 |
| All batches, A to G | About 10 to 13 |
| Used so far: batches A to E | About 5.5 |

Batches E and F cost the most, because they change report generation and need the inputs listed in the decisions table.
