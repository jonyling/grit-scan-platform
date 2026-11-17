# 46. Adverse news and lessons learnt scan

**Status:** Built, 2026-11-05.
**Trigger:** Wilson's prompt, [Adverse_News_Lessons_Learnt_Scan_Prompt.docx](../docs/Adverse_News_Lessons_Learnt_Scan_Prompt.docx), which unblocks improvement point 27 in [the improvement plan](../docs/progress/GRIT-Scan_Improvement-Plan_2026-10-26.md).
**Builds on:** specs 30 (source credibility), 37 (the Group pack and its merged graph), 42 (the daily lane and events), and 45 (critique and page verification).

## 1. What it does

Once a week, `grit adverse` writes the Adverse News & Lessons Learnt Scan as an email, ready for Jony to paste into a new message to Wilson. The scan has these parts:

- the Immediate Escalation Summary;
- Section A, adverse news directly relevant to ST Engineering;
- Section B, competitor and industry adverse news, with lessons learnt;
- Section C, emerging risk signals;
- the Management Attention Summary;
- the Final Validation.

The prompt asks an analyst to judge each item. GRIT-Scan keeps its rule: agents propose, deterministic functions decide, and a named human approves. The agents answer the prompt's five tests and propose each field. Rules check every field against the sources and the register, and cap what the evidence cannot carry. The command sends nothing. It writes files and prints the email.

```bash
.venv/bin/grit adverse --date YYYY-MM-DD
```

| Flag | Effect |
|---|---|
| `--dry-run` | Prints the window, the planned queries and the candidate counts. Sends no query and calls no model. |
| `--no-search` | Uses the pool only: no Brave query. |
| `--limit-queries N` | At most `N` Brave queries, including corroboration searches. Default 40. |
| `--since YYYY-MM-DD` | Overrides the start of the reporting period. |
| `--workers N` | Assessor calls in flight at once. Default 4. |

## 2. Inputs

| Input | Where | Role |
|---|---|---|
| The pool | `output/grit.sqlite` | Every page the weekly, daily and adverse lanes found in the reporting period. |
| The merged graph | Group pack `ste`: its business-area children `dsc`, `urs` and `ca` | STE entities, customers, suppliers and competitors, with their business areas. |
| The watchlist | `config/adverse/Adverse-Watchlist.json` | Names the registers lack: the Defence, Marine, Land Systems, Satcom and US businesses, their programs, Singapore defense customers, JV partners, and defense, satcom and MRO peers. Recorded on Jony Ling's instruction, 5 November 2026, without Wilson's review. |
| The lexicon | `config/adverse/Adverse-Lexicon.json` | Adverse terms for the A1–A7 and Section B categories, and change terms for Section C. A screen, never a verdict. |
| The scan plan | `config/adverse/Adverse-Scan-Plan.json` | The weekly Brave queries: the group, subsidiaries and programs, customers, enforcement sources, and peers. |
| Internal context (optional) | `secrets/adverse/internal-context.json` | Where STE people are, by country: Middle East local staff by entity and country, and travellers and assignees by country, each with an as-at date. Supplied by R&A on 17 November 2026 (`grit/adverse/people.py` reads it). Country level only: no city or site. |

The file is one JSON object. `middle_east_local_staff` has `as_at` and `entries` of `{entity, country, headcount}`. `middle_east_travellers_and_assignees` has `as_at` and `by_country` (`{country: count}`). `entity_level_examples` lists `{entity, country, staff}`. Country names are normalised: "UAE (Dubai)" is United Arab Emirates. Each country also matches its people and main cities in news text, such as "Saudi", "Riyadh" or "Doha".

How the scan uses it:

- **Search.** One search per country with STE people, most people first, up to `peopleLimit` (default 5) on top of `queryLimit`: `"Qatar" missile OR drone OR attack OR evacuation OR unrest`.
- **Screen.** A page whose title or lead hits the lexicon's Threat to people category (missiles, strikes, evacuations, travel warnings, unrest, disasters) and names a country with STE people is a Section A candidate, link `PEOPLE`, even when no page names ST Engineering. Such pages that share a country join one item before assessment, so one country's security situation takes one slot of the Section A cap. An outlet suffix such as "\| The Times of Israel" does not count as naming a country.
- **Exposure.** For a safety or security item (category A5, or a Threat to people hit), STE people in the country make the exposure Indirect exposure, marked as coming from the staff log, not a news source. A Safety impact is added, and Monitor becomes a staff-safety follow-up for Group Health, Safety and Environment. A financial or other story is not made an exposure by staff presence.
- **Context.** Any Section A or C item that names such a country lists the staff in it.
- **Validation.** The Final Validation gives the as-at dates, the countries, the items matched, the exposures set from the log, and the caveats. Data older than 30 days is flagged as stale.

## 3. Reporting period

The period runs from the end of the previous completed scan (`adverse_scans`, status `READY`) to the run date, both inclusive. The one-day overlap is safe because duplicates are removed (section 7). With no previous scan, the period is the seven days that end on the run date. Wilson's prompt says 24 hours. The weekly cadence needs seven days (Jony Ling, 5 November 2026).

A page is in the period when its publication date is. A page with no stated publication date counts by the day GRIT-Scan first saw it, and the item says so. An older event enters only as a material new development (section 7).

## 4. Stages

1. **Plan and search.** The scan plan's queries go to Brave for exactly the reporting period, within the monthly guard. A query answered in the run is not sent again.
2. **Screen by rule.** Every pool page in the period whose title or first 400 characters hit the lexicon is linked to the merged graph and the watchlist (the link ladder, spec 37). Its provisional section follows from the rung:

   | Rung | Provisional section |
   |---|---|
   | DIRECT (an STE entity, program or the group) | A |
   | COUNTERPARTY (a registered customer, supplier or JV partner) | A |
   | COMPETITOR, or an enforcement query | B |
   | PERIMETER or SECTOR, or a strong Group-theme hit, with a change term | C |

   Each section's candidates are ranked by rung, source band (spec 30), lexicon hits, outlets, and whether a linked name and an adverse term share the title. They are then capped: A 200, B 80, C 60.
3. **Screener.** The model reads each candidate's title and lead in batches of 25 and proposes A, B, C or NONE. A Section A proposal needs a DIRECT or COUNTERPARTY link. NONE drops the candidate. If a batch still fails after one retry, the scan is `INCOMPLETE`: a model outage never reads as "nothing found".
4. **Group and verify.** Candidates join into one item per event: the nightly index's event, else the same linked name and a similar title. At most 30 Section A items, 20 Section B items and 20 Section C items go on. The rest are listed for the reviewer. Each source page is opened with `verify_page` (public HTTPS, at most 1 MiB, up to three redirects to the same page). A page with fewer than 400 characters of text, or a bot check, does not count as opened. Many publishers block automated requests from a server, so when a page does not open, the search engine's text for it (Brave's description and extra snippets, kept in the pool) is used instead if it has 300 characters or more. Opened pages come first. Such a source is marked `SEARCH_EXTRACT`, and the rules treat it like an opened page (Jony Ling, 10 November 2026). The email labels it as search-engine text, and the reviewer worksheet lists it for a spot check. An item with no opened page and no usable search text is withheld and listed for the reviewer.
5. **Assessor.** For each item, the model reads the verified source text (opened pages and any search-engine text, stage 4), the register facts, STE's footprint (business areas, lines of business, and countries where STE entities are registered) and any earlier report of the event. It answers the five tests and proposes the section's fields (section 5). For Section C, the source supplies the facts and the Assessor makes the connection from the footprint. The consequence and pathway are what could reasonably follow from the evidenced change. The fields are checked (section 6). A failing draft gets one retry with the violations. A draft that still fails is withheld and logged.
6. **Merge and corroborate.** Items in one section with similar headlines merge: the higher-rated item keeps its text and gains the other's verified sources and quoted facts. A Critical or High item with one independent outlet and no official source then gets one Brave search, within the query limit. The item records whether it was corroborated.
7. **Critic.** One call reviews every included item against the prompt's "never create risks" rules. It can keep, downgrade or withdraw an item, naming one rule. A withdrawal removes the item. A downgrade lowers the severity one step and weakens an overstated exposure label. The Critic also lists groups of items that report the same event, such as one breach reported by several customers. Each group in one section merges as in stage 6. A group across Sections A and B is left alone, because Section B stays separate.
8. **Decide and render.** Rules set the final severity, action, escalation, the two summaries and the overall assessment (section 6). The email and the reviewer worksheet are written.

## 5. Fields

The Assessor proposes the prompt's fields only from the values the prompt lists:

| Field | Values |
|---|---|
| Section A category | A1 Financial, A2 Project delivery, A3 Regulatory and compliance, A4 Cyber and security, A5 Safety, A6 Reputation, A7 Strategic |
| Relationship to ST Engineering | Customer, Supplier, Joint venture partner, Consortium partner, Government stakeholder, Major subcontractor, Critical technology provider, Competitor, Other relevant stakeholder |
| Exposure assessment | Direct exposure, Indirect exposure, Relationship identified, but material dependency not confirmed, Exposure unknown, No confirmed ST Engineering exposure |
| Impact area | Revenue, Profitability, Cash flow, Project delivery, Operations, Compliance, Cybersecurity, Safety, Reputation, Strategic objectives, each Confirmed, Potential or Not yet established |
| Severity | Critical, High, Medium, Low |
| Recommended action (A) | Monitor, Follow-up with Business Area, Follow-up with Group Function, Escalate to EXCO |
| Risk category (B) | The prompt's 13 categories |
| Coverage (B) | Yes, Partially, No, Unable to determine |
| Time horizon (C) | Immediate, Near term, Medium term, Longer term, Unable to determine |
| Recommended response (C) | Monitor, Obtain further information, Follow-up with Business Area, Follow-up with Group Function, Escalate to EXCO |

Each item separates three kinds of statement: facts, each with a verbatim quote from a verified source (an opened page or search-engine text, stage 4); the evidence-based assessment; and what remains unverified.

## 6. Rules

The model proposes. These rules decide.

| Rule | Check |
|---|---|
| Grounding | Every fact quotes a verified source verbatim, 20 characters or more. Every number in the prose appears in the cited sources. No URL in prose. Links come from the pool. |
| Dates | The publication date comes from the pool. Without one, the Assessor's date needs a verbatim quote that contains the year. Otherwise the item says "not stated by the source" and cannot be High or Critical. An event date other than "not stated" needs a verbatim quote. |
| Section | A needs a DIRECT or COUNTERPARTY link, or a PEOPLE link (a threat to people in a country where the staff log places STE people). A competitor-only item moves to B. |
| Relationship | Must match the register: a customer is Customer or Government stakeholder; a supplier is Supplier, Major subcontractor or Critical technology provider; a JV partner is Joint venture partner. A competitor is Competitor. An item about ST Engineering itself, a subsidiary or a program reads "ST Engineering entity", because the prompt's list has no value for the group's own entities. A watchlist joint venture reads "Joint venture partner". |
| Exposure | Direct or Indirect exposure needs an ST Engineering name in a verified source. Otherwise a registered relationship reads "Relationship identified, but material dependency not confirmed". An item with no relationship reads "Exposure unknown". |
| Severity cap | Critical needs Direct or Indirect exposure and a verified source. High needs a verified source and a stated publication date. Critical and High record whether they were corroborated. |
| Action | Critical is "Escalate to EXCO". "Escalate to EXCO" needs Section A, Critical or High, Direct or Indirect exposure, and one of the prompt's mandatory EXCO triggers. Otherwise it becomes "Follow-up with Group Function". A named Business Area must be the linked entity's own business area. A Group Function comes from the fixed list. |
| Section B | Lessons must not state or imply that ST Engineering has the weakness. A control weakness needs a verbatim quote, or reads "The publicly available information does not yet establish the underlying control weakness." Coverage is "Unable to determine" while no internal Risk Radar, CSA or policy material is supplied. |
| Section C | A time horizon other than "Unable to determine" needs a verbatim quote. The connection must name a business area, line of business or registered name from the supplied list. |
| Escalation summary | Section A items rated Critical or High that name at least one of the prompt's immediate-escalation triggers. |
| Same topic | After the Critic, items that share a linked entity, or whose headline names an entity another item is linked to, become one item across sections. The Group, business-area and unit nodes are too broad to make a topic. The item in Section A, then C, then B, then the highest rated, keeps its text. The others add their sources, quoted facts, lessons and trigger points, and are listed under it as related reports. |
| Currency check | Each item's main entity is searched again over the 72 hours to the report date, up to `currencyLimit` searches (default 8) on top of `queryLimit`. Items with confirmed exposure go first, then Sections A, C and B, then by severity. Up to three newer headlines that name the entity and are not among its sources are listed under the item, flagged with any lexicon terms. They are not assessed. |
| Immediate Attention | Section A items with Direct or Indirect exposure and a follow-up action, and any item recommended for EXCO, highest severity first, at most four. A follow-up with no confirmed exposure, such as a generic vulnerability or threat, is listed under Enhanced Monitoring with the reason. |
| Overall assessment | Immediate Escalation Recommended if any item is escalated to EXCO; Management Action Recommended if any item is in Immediate Attention; Elevated Monitoring Required if any item is monitored; otherwise No Material Concern. |

## 7. Duplicates and material updates

`adverse_items` keeps every item each scan included. A candidate repeats an earlier item from the last 120 days when it shares a page or an event with that item, or names the same entity with a similar title. The Assessor sees the earlier item and answers NEW, MATERIAL_UPDATE or NO_MATERIAL_UPDATE. A material update needs a verbatim quote of the new fact, and that quote must not appear in the earlier item. A repeat with no material update is left out, and its section says "No material update from the previous reporting period."

## 8. Outputs

Each run writes to `output/adverse/DATE/`:

| File | Contents |
|---|---|
| `email.txt` | The email to paste: `To: wilsonweimeng.yeo@stengg.com`, `Subject: Adverse News & Lessons Learnt Scan \| D Month YYYY`, "Dear Wilson," and "Kind Regards, Jony". Written only when the scan is `READY`. Otherwise the file is `email-DRAFT-INCOMPLETE.txt`. |
| `email.eml` | The same message, to open in Outlook. |
| `email.md` | A Markdown preview. |
| `report.json` | The decided items, the window, the checks and the status. |
| `review.md` | The reviewer worksheet: blockers, withheld items and why, sources checked only against search-engine text, repeats, the Critic's objections, and the searches. |

The email carries the standard notice that the content is GRIT-Scan AI-generated suggestions, which a named person reviews before anyone acts on them. The Final Validation is computed from the report's own records: how many sources were opened pages and how many search-engine text only, how many facts rest on search-engine text, which items rest on nothing else, missing dates, same-topic merges, the currency check, and whether internal context was supplied. The email accuracy check confirms the recipient, the subject line, the greeting and the closing.

## 9. Schedule and cost

`grit scheduled` runs `grit adverse` every Thursday after the business-area scans due that day, so it reads their pages. `--no-adverse` leaves it out. A week costs at most 53 Brave queries (36 planned, up to 5 staff-country searches, up to 4 corroboration searches and up to 8 currency checks), about 210 to 260 a month, which is above the free Brave credit. Model calls: up to 13 screening batches, up to 70 assessments with retries, and one or two Critic calls. On the 5 November 2026 pool, with no searches, a run took about 10 minutes.

A first trial on 5 November 2026 (a copy of the pool, `--no-search`) screened 6,601 pages, verified 33 source pages, and reported 4 Section A and 4 Section B items. The Screener's choices vary from run to run by a few candidates at the margin. The rules and the Critic hold the reported set steady.

## 10. Known limits

- Section B's Risk Radar, CSA and control coverage stays "Unable to determine" until R&A supplies that material.
- Pages that refuse automated readers (for example Bloomberg, Forbes and The New York Times) cannot be verified, so their items are withheld for the reviewer even when the story is real. Another outlet's report of the same event is used when it opens.
- Section C is strict: in the first trial, the Assessor excluded every emerging signal for lack of a specific route to an STE business. That follows the prompt's "do not force findings", but Wilson should confirm the bar.
- The watchlist is from public sources. Wilson and the business areas have not reviewed it.
