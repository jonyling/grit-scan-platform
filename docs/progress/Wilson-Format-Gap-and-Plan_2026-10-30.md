# Bring the GRIT-Scan brief to Wilson's format

This page compares the GRIT-Scan URS brief with Wilson's hand-prepared report, *URS GRIT Scan, BA-wide, 22–29 October 2026 (v2, final-checked)*. It maps the six requests of 30 October 2026 to the gaps and proposes one change-controlled release to close them. Use it to agree scope with Hwai May and Wilson before any build starts.

## Status

Steps 1 to 5 were built on 30 October 2026 (spec 44, commit `36526b2`). URS, DS&C and CA scenario registers are recorded as approved on Jony Ling's instruction, for the POC, with BA review pending. Step 6 is not started.

## Context

Hwai May froze the requirements on 28 October 2026, as Wilson's report records in section 4b. The six requests below change the report's layout and add forward-looking content. They are not new intelligence sources. The proposal is to accept them once, as **brief format v2**, written as spec 44, and to hold every later request for the MVP backlog.

## How the two reports differ

| Area | Wilson's report | GRIT-Scan brief today |
|---|---|---|
| Organising principle | Event-led. Each event (A1 Saudi–Houthi, A2 US–Iran, C1 Taiwan) is tagged with the risks it touches: "[Key Risks 5, 3, 2]". | Risk-led. One row per URS risk theme; events appear only as evidence under a theme. |
| Top of report | Status word and arrows (ELEVATED; Saudi ▲, US–Iran ▲, Taiwan typhoon ▼), bottom line, then the status rationale and the Red rule. | A coloured bottom-line box (STABLE, WATCHPOINT or RED) and one paragraph. |
| Escalating items | Band ▲ NEW / ESCALATING, with a dated timeline, casualties and aviation figures, and the URS position for each event. | Escalating themes in the key-risk table. No timeline per event. |
| What changed since the last report | Table: item, last report, this report, direction. | A trend column ("vs 21 Oct") per theme only. |
| Look-ahead | Table of dated events for the next 7–30 days, such as US midterms on 3 Nov and the RSC on 6 Nov, each with its URS relevance. | None. |
| Prioritisation | Table: item, trend, risk linkage, owner or forum. | Priority findings as prose. No owner column. |
| Coverage | LOB × Operational, Financial/macro, Supply/cost, Competitor/tech and Regulatory/legal. Each cell is a finding, a nil, or "Not searched". | Not shown. The method note gives counts only. |
| Jurisdictions | Table of 13 countries: URS entities, the in-window finding, and a trend arrow. | Not shown. |
| Exposure snapshot | People, entities and assets, Qatar and UAE, Taiwan, financial calibration (EBIT bands), RSC reporting. | Order-book baseline (URS Taiwan only). |
| Suppliers and partners | Named partners re-checked, and a list of those not re-run. | A suppliers section, prose. |
| Actions | One ask, the Red triggers, and follow-ups grouped by owner. | Response options under key risks, marked as suggestions. |
| Quality check | Self-challenge gate (seven questions), corrections, and conflicts and caveats. | QA and correction log. |
| Sources | Section 8 source register, all links at the end, tiered T1 to T4. | Links inline under each finding. |
| Scenarios | None. | Best, medium and worst scenarios for the Group pack only. |
| Inputs | Public web **and internal material**: GRIT alerts, EXCO travel tracker, RSC packs, R&A emails. | Public web and the BA workbooks only. |

The last row matters most. About a third of Wilson's content comes from internal documents GRIT-Scan cannot see: staff locations, the 13 Oct HR safety confirmation, the Item 5 Taiwan figures and the RSC timetable. No format change closes that gap. The plan below adds a governed way to bring those facts in (step 6).

## Map of requests to gaps

| Request | Gap it closes | Step |
|---|---|---|
| 1. Scans and management requests in tables, scan-led, mapped back to the risk (Wilson's format) | Organising principle; prioritisation | 1, 2 |
| 2. Top-of-report table for escalating and elevated items: source, summary, implications, suggested response (YK's request) | Escalating items | 2 |
| 3. Other sections as short tables by LOB, operational, competitor, supplier, financial, supply chain and country, with links in an appendix | Coverage; jurisdictions; suppliers; sources | 1 |
| 4. Review against Wilson's report and be more specific | Quality check; specificity | 5 |
| 5. Forward-looking and predictive elements | Look-ahead; what changed | 3 |
| 6. Best, medium and worst scenarios | Scenarios for URS | 4 |

## Plan

Each step is one working session or less. Steps 1 to 3 can be built without new inputs; steps 4 and 6 need people.

### Step 1: Layout in Wilson's order (request 1, 3)

Rebuild the Word brief, and the slide deck to match, with these sections:

1. Header: status word (STABLE, ELEVATED or RED) with ▲ = ▼ per active event, the bottom line, and the status rationale with the Red rule.
2. Escalating and elevated items table (step 2 fills it).
3. What changed since the last report.
4. Look-ahead, next 7–30 days (step 3 fills it).
5. Prioritisation table: item, trend, risk linkage, owner or forum. The owner comes from the scan plan's audience and the theme's owner where the register names one, otherwise it is blank.
6. Coverage matrix: LOB × the five categories. A cell is a finding with its reference, "Nil: searched", or "Not searched this week", decided from which queries actually ran. Nothing is claimed as nil without a search.
7. Jurisdiction table: every country in the entity register, its URS entities, the in-window finding and the trend.
8. Named supplier, customer and partner watch: one row per name with a finding, and a line listing the names not searched this week.
9. Risk themes table (today's table, kept as the risk view).
10. Scenarios (step 4).
11. Quality check (step 5).
12. Appendix: source register. Every link moves here, numbered [S1], [S2], and tiered: T1 official or primary (government and regulator domains), T2 HIGH-rated, T3 MEDIUM-rated or regional, T4 aggregator or unrated. Body text cites [S#] only.

### Step 2: Event register and the top table (request 1, 2)

- Group the week's kept signals into events. The nightly index already clusters stories into events; the brief will use those clusters.
- Give each event its risk linkage from the link ladder and theme matches, such as "[URS Key Risk 5; Tier-2 Risk 7]", and its management request where one matched.
- Band each event ▲ new or escalating, = continuing, or ▼ easing, by rule against the previous report.
- For each ▲ and elevated event, the Assessor drafts one row: source, summary (with dates and figures from the sources), implications for URS, and suggested response. The usual checks apply: every figure must appear in a cited source, and the response is marked "Suggestion for the business area to consider". A row that fails its checks twice is withheld and logged.

### Step 3: Forward-looking content (request 5)

- **Look-ahead table.** Extract dated future events (elections, central-bank meetings, tender closings, results dates, sanction deadlines) from the week's sources. A date is kept only when the cited source states it. Add the RSC and other dates from the internal-notes file (step 6).
- **Leading indicators.** Print the five indicators with their bands (already built, proposed bounds) under the look-ahead table.
- **Risk window.** Where an event names a date range, such as "after 3 Nov", the header shows it, as Wilson does.
- No probabilities. The brief says what is scheduled and which signposts were seen, never odds; that is the existing rule.

### Step 4: Best, medium and worst scenarios for URS (request 6)

The scenario machinery (spec 43) runs for the Group pack today. For URS:

1. Run the Strategist for the URS pack: scenarios for the Middle East conflict and the Taiwan Strait, with signposts and planned responses.
2. Hwai May reviews, and Wilson approves.
3. The URS brief then shows where each scenario leans, the signposts seen, and the responses triggered.

This needs Hwai May's review time; the build itself is small.

### Step 5: Specificity and the quality check (request 4)

- **Self-challenge gate**, generated by rule from what ran: macro checked, all LOBs swept, competitors checked, export controls checked, and every nil searched. The answers are "Yes", "Partly" or "No", with the reason.
- **Specificity checks** on every drafted summary: it must give a date, a named actor or place, and a figure where the source has one. A summary that says only "tensions rose" fails and is redrafted.
- **Conflicts and caveats**: where two cited sources disagree, as with the KKIA reopening date, both are shown.
- Compare a week's brief with Wilson's report side by side, and log each miss as a scan-plan fix.

### Step 6: Internal facts the web cannot supply (inputs)

Wilson's exposure snapshot uses staff counts, safety confirmations, asset values, EBIT calibration bands and RSC dates. Add one internal-notes file per BA, which the business partner updates weekly, with each line dated and attributed. The brief quotes it as "Internal: [BP name], [date]". This is the VS Code and document-upload feedback route from improvement point 28. Without it, those cells stay blank and say so.

## Effort and order

| Step | Sessions | Needs |
|---|---|---|
| 1 Layout | 1 | Nothing |
| 2 Event register and top table | 1.5 | Nothing |
| 3 Forward-looking | 1 | Nothing |
| 5 Specificity and quality | 0.5 | Nothing |
| 4 URS scenarios | 0.5 build | Hwai May's review, Wilson's approval |
| 6 Internal notes | 0.5 | A BP who updates it weekly |
| Total | about 5 | |

Recommended order: 1, 2, 3, 5, then 4 and 6 as the people inputs arrive.

## What stays out

- GRIT-Scan does not read Outlook, EXCO trackers or RSC packs directly. Internal facts come only through the internal-notes file.
- No probabilities or odds. Scenarios show signposts seen.
- Requests after this release go to the MVP backlog, through the SDD specification process.
