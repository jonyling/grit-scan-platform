# 38. Report library and State of the Situation

**Status:** Draft for review, 2026-10-15. Extends spec 37.
**Trigger:** The R&A team's decision of 14 October 2026. Forecasts and recommended options need a deeper base than a month of news: a State of the Situation (SotS) for each of the 12 Group themes and 8 strategic shifts, built from the thought-leadership reports in Wilson's email of 9 October 2026.

## 1. Layers

| Layer | What it is | Built from | Refreshed |
|---|---|---|---|
| State of the Situation | History, structural drivers, key actors, where things stand, key uncertainties, indicators to watch, and STE's exposure | The report library and the business-area registers | Yearly, or when a report has a new edition. Wilson signs it off. |
| Monitoring | What moved this month against the State of the Situation | News search and primary sources | Monthly (spec 37) |
| Forecasted outcomes | Scenarios built on each document's key uncertainties | The State of the Situation and recent signals | Quarterly |
| Recommended options | Responses tied to the uncertainties and to existing controls | The State of the Situation, the scenarios, and the risk registers | Quarterly, and when a theme escalates |

This spec covers the first layer. The other three build on it.

## 2. Topics

There are 11 documents, not 20. Nine of the 12 dashboard themes sit under a strategic shift and take their monitoring baseline from that shift's document. The other three have their own documents.

| Document | Covers | Library coverage |
|---|---|---|
| Middle East defence and infrastructure spending | Shift 4; themes 1, 2 | Good |
| Ukraine reconstruction | Shift 5; theme 4 | Good |
| Semiconductor and GPU access | Shift 3; themes 6, 7 | Good |
| Quantum-safe security migration | Shift 7; theme 10 | Good |
| AI and autonomous systems | Shift 1; theme 8 | Good |
| Sovereign AI and digital sovereignty | Shift 2; theme 9 | Thin |
| Export controls and technology blocs | Shift 6; theme 11 | Thin |
| Human-machine teaming | Shift 8 | Gap |
| China-Taiwan tensions | Theme 5 | Thin |
| Defence budgets globally | Theme 3 | Gap |
| Competitor strategic moves | Theme 12 | Gap |

`grit/library/catalogue.py` lists each topic's reports, and proposes sources for each gap. Wilson approves any addition to the library.

## 3. Report library

`catalogue.REPORTS` registers each report: its file under `secrets/reports/`, title, publisher, edition, and whether it is public or internal. An internal report, such as the DS&C risk refresh, is cited inside STE only. A report written by another tool is not a source: Copilot's URS brief is kept for comparison and never read.

`grit library-ingest` reads each report page by page (a PDF page, a slide, or a part of a Word document). It packs whole sentences into passages of about 1,200 characters and embeds them. The passages and vectors stay under `secrets/library/`, because some reports are internal. A report whose file has not changed is not read again. A registered report with no file is listed as missing.

For each section, retrieval takes the passages that best answer the topic question with the section's angle. It reads only the topic's reports. Each report gives its two best passages first, so a two-page country brief is not crowded out by a 150-page global report. The rest go by score, at most five from one report.

## 4. Situation Analyst

The Situation Analyst drafts each section from at most 12 passages, numbered P1, P2, and so on across the document, so a passage keeps one id. The checks withhold a section that fails three times:

- Every sentence cites a supplied passage.
- Every number in a sentence appears in the passages that sentence cites.
- A sentence with forecast language ("will", "projects", "expected to") names the publisher that makes the forecast, or says "according to".
- At least one quote is verbatim from a cited passage.
- A narrative section has at least 150 words. It can be shorter only when `not_covered` says what the library lacks.
- No URL and no governed score word.

`not_covered` is required wherever the passages cannot support what the section needs. The library's gaps stay visible and are never filled from the model's memory.

The "In brief" is written last, from the validated sections and only the passages they cite.

## 5. STE exposure

Code writes this section, never the model. It lists:

- the business areas' entities registered in the topic's countries;
- the suppliers and customers whose register names match the topic's counterparties or countries;
- the business areas' own risk themes that share the topic's vocabulary.

It is proposed until the business areas confirm it.

## 6. Output and sign-off

`grit sots` drafts the four pilot topics. Use `--topic` for others and `--all` for all 11. Each draft goes to `output/sots/DATE/` as Word, Markdown, and JSON, with an `audit.jsonl` of every model call: prompt version, hashes, attempts, and violations.

| Option | Use |
|---|---|
| `--redraft SECTION --from DATE` | Draft only the named sections of a saved draft again. |
| `--render-only DATE` | Render the saved drafts again, with the exposure recomputed. It makes no model call. |

In-text citations name the report, the edition, and the pages, such as "(PwC Semiconductor, Nov 2025, p. 64)". The Sources list gives each report's full title, edition, the pages cited, and its link. Wilson edits the Word document and signs it off.

## 7. Acceptance

- A sentence without a citation, a number not in its cited passages, or an unattributed forecast withholds the section.
- A topic with no report makes no model call, and states that the library does not cover it.
- An unchanged report is not read or embedded again.
- The exposure section names only what the registers hold.

The pilot of 15 October 2026 drafted all four pilot topics. All 28 sections passed their checks; two needed a third attempt.
