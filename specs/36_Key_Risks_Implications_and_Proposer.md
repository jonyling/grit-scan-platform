# 36. Key risks, implications, and the Proposer

**Status:** Draft for review, 2026-10-13. Extends spec 35.
**Trigger:** Comments from the Risk & Assurance reviewer (Yikai) on the first Digital Systems & Cyber (DS&C) brief:

1. Focus on the Key Risks in sheet 7 of the Entity Info workbook, and on the more material news.
2. State what a Key Risk signal means for the business area.
3. Propose options for management to consider.

This spec applies to every business-area pack whose standing items are risk themes, including Urban Solutions. It does not change an entity pack's standing conditions.

## 1. Key risk focus

A Key Risk is a sheet 7 theme with tier `KEY`. A business area has six.

### Evidence order

The Assessor reads at most 12 sources for each theme. A deterministic function chooses them and orders them most material first. It sorts by, in order:

1. a RED signal, or a harm verdict of high negative impact;
2. the link rung: `DIRECT`, `PERIMETER`, `COUNTERPARTY`, then the rest;
3. a management-request match;
4. briefing tier;
5. the number of outlets that carry the event;
6. publication date, newest first.

No model decides materiality.

### Search weight

The scan plan gives each Key Risk more queries than a tier-2 or emerging theme. Draw the queries from the theme's sheet 7 drivers. This is a data change in `PREFIX-Scan-Plan.json`.

### Brief layout

In the standing-items section, each Key Risk, and any escalating tier-2 or emerging theme, is one row of a table with the R&A reviewer's columns:

| Column | Content |
|---|---|
| News/Developments | The theme, then the Assessor's validated summary without inline references. |
| News/Information Source | Each cited outlet, numbered and linked, with the article title. |
| Date and Time | Each source's publication date, with the time when the source states one. |
| Relevance/Potential Impact to the business area | The implication. The header uses the pack's short label, such as DS&C. |
| Proposed Response/Management Consideration | The Proposer's options, each with its kind, owner, what it builds on, and its trigger. The column appears only when the profile includes `RESPONSE_OPTIONS`. |
| Priority/Trend | The tier, the reader's best news priority among the sources, the trend, and the confidence. |

The Word brief puts this table on a landscape page. The Markdown copy and the Excel `Key risks` sheet use the same columns. Every other theme stays one row in the standing-items table.

### Executive-summary deck

The PowerPoint deck follows `docs/reference/GRIT-Scan_Executive_Summary.pptx`. It is built on a template trimmed from that deck (`grit/report/templates/executive_summary.pptx`, rebuilt with `python -m grit.report.deck_template`), so it carries the ST Engineering master and branding.

1. **Cover.** The business area, the scan window, the reader, the report owner, and the AI-generated content statement.
2. **Executive summary.** One to three slides. One slide covers up to two critical items, two slides cover three or four, and three slides cover five or more. A critical item is a key risk or an escalating theme. On a Group pack it is a condition with evidence this period. Each slide shows at most two full cards. Items past the sixth are named in full on the last slide. The first slide carries the status badge and the bottom line.
3. **Appendix, one slide per item.** What it means for the business area, what happened, and, when the profile includes `RESPONSE_OPTIONS`, the options. A side panel gives the risk linkage, the owners of the options, and up to three sources.

Each card states what the point means for the pack, using the pack's short label, for example "What it means for DS&C". A summary card keeps whole sentences and does not use a truncation mark. When the first sentence is longer than the box, the box wraps that sentence. An appendix box may still shorten text with an ellipsis. Each summary card links to its appendix slide, and each appendix slide links back to the first summary slide. The Word brief keeps the full text.

Every Word brief, Markdown preview, and slide carries this statement, on the cover and again in the footer:

> GRIT-Scan AI-generated suggestions for consideration. The scan can miss a source, misread a page, or leave out context. A named person reviews this before anyone acts on it.

A repeated sentence, or a later sentence whose figures were all used earlier, is kept at its first use. A table cell stays as written. The statement above can repeat. A finding shows a suggestion only when a Proposer option or a triggered scenario response already cites that signal. The line starts with "Suggestion for the business area to consider." A signal with no such action has no suggestion.

## 2. Implication

The Assessor writes an `implication` for a Key Risk when the week's evidence is validated. It is one or two sentences on what the development could mean for the business area.

The checks withhold an implication that fails any of these rules:

- It names at least one of the business area's own anchors: a line of business, a LOB grouping, an STE entity, a registered customer, or a registered supplier. The anchors come from the typed graph. The business area's own name alone is not enough.
- It is conditional. It uses `could`, `may`, or `might`, and it does not use `will` or another certainty word.
- It cites at least one of the assessment's own sources.
- It adds no number that is not in a cited source, and it uses no governed score word.

A withheld implication leaves the assessment standing. The QA log records why.

## 3. Proposer

The Proposer is a seventh agent role. It proposes options for management to consider on one Key Risk. It never decides, scores, or approves.

### When it runs

It runs for a Key Risk whose assessment is validated and that either is escalating or cites a material source. A material source has a `DIRECT`, `PERIMETER`, or `COUNTERPARTY` rung, a P1 or P2 priority, or briefing tier 1 or 2. A business area has at most six Proposer calls a week.

### Input

- The theme: its name, drivers, key controls, and action plans from sheet 7.
- The validated assessment and implication.
- The same sources the assessment cites.
- The owner roles: each LOB head and grouping head from the LOB hierarchy, plus a fixed list of Group functions.

### Output

```json
{"reasoning": "the Proposer's working, before it answers",
 "options": [{"kind": "STRENGTHEN_CONTROL | ACCELERATE_ACTION_PLAN | ADDRESS_GAP | MONITOR",
              "option": "Consider ...", "owner": "an owner role from the input",
              "builds_on": "a verbatim span of the key controls or action plans, or empty",
              "trigger": "for MONITOR: what would change the response",
              "source_ids": ["S1"]}]}
```

### Checks

The checks withhold the options that fail any of these rules. One retry with the violations is allowed.

- Two to four options, and exactly one `MONITOR` option with a trigger.
- Each option starts with `Consider` and does not use `must`.
- The owner is one of the supplied roles. No person is named.
- `STRENGTHEN_CONTROL` quotes the key controls, and `ACCELERATE_ACTION_PLAN` quotes the action plans, verbatim, 20 characters or more.
- Each option other than `MONITOR` cites at least one supplied source.
- No number that is not in the cited sources or the theme text, no URL, and no governed score word.

### Chain of thought

The prompt asks the Proposer to reason before it answers, in the `reasoning` field. The reasoning is stored in the execution log entry for the call, with the prompt version and the response hash. It is never stored with the options, and the brief never shows it. A reviewer can read it to see why an option was proposed. It is not evidence: the cited controls and sources are.

### Governance

The brief labels the options "Proposed by GRIT-Scan for management consideration. Not reviewed advice." They are part of the reader's package, so they pass `ReviewGate()` with the rest of the brief. A reader sees them only when the profile's brief sections include `RESPONSE_OPTIONS`.

## 4. Data

- `condition_assessments` gains `implication` (text) and `options_json` (the checked options, without the reasoning).
- The themes register gains `sheetActionPlans` when the pack's `build.themeActionPlans` is `true`. It is opt-in, so an existing pack's registers stay unchanged until it is rebuilt with the flag.

## 5. Acceptance

- A Key Risk's sources are ordered by the materiality rule, not by date.
- An implication that names no anchor, or reads as a forecast, is withheld, and the assessment stands.
- The Proposer is not called for a theme that is not a Key Risk, or for a Key Risk with no validated, escalating, or material evidence.
- An option with an unknown owner, an unquoted control, or an uncited source is withheld.
- The reasoning is in the execution log and nowhere in the brief.
- A profile without `RESPONSE_OPTIONS` sees no options.
