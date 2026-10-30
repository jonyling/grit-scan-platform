# 44 Brief format v2: Wilson's BA-wide layout

Status: built on 30 October 2026. This spec records requests 1 to 5 of 30 October 2026 and the plan in [Wilson-Format-Gap-and-Plan_2026-10-30.md](../docs/progress/Wilson-Format-Gap-and-Plan_2026-10-30.md). Request 6, internal facts from a business partner's notes, is not built.

## 1. What changes

The weekly brief follows the order of Wilson's report *URS GRIT Scan, BA-wide, 22–29 October 2026 (v2)*. It is event-led: the week's news is grouped into events, each mapped back to the register risks it touches. The risk-theme table stays as the risk view.

| Order | Section | Source |
|---|---|---|
| Header | `STATUS: STABLE`, `ELEVATED` or `RED`, a ▲ = ▼ arrow per event, and the next dated items | `grit/report/format_v2.py` |
| Box | Bottom line | Reporter (unchanged) |
| 1 | Escalating and elevated items: item and risk, summary, implications, suggested response with owner, sources | `grit/pipeline/events.py`, EventAssessor |
| 2 | What changed since the last report | Rule |
| 3 | Look-ahead: the next 30 days, and the leading indicators | `grit/report/lookahead.py` |
| 4 | Prioritisation: item, trend, risk linkage, owner or forum | Rule |
| 5 | Coverage matrix: line of business × Operational, Financial / macro, Supply / cost, Competitor / tech, Regulatory / legal | Rule |
| 6 | Jurisdiction coverage: each country with registered entities or assets | Rule |
| 7 | Named supplier, customer and competitor watch | Reporter's item entries |
| 8 onwards | The profile's sections: risk themes, scenarios, management requests, entity packs, entities, priority findings, QA log, watchpoints | Unchanged |
| Last but one | Quality and completeness check: the self-challenge gate and conflicts between sources | Rule |
| Appendix | Source register: every cited source as `[S#]`, with outlet, title, date, tier and link | Word renderer |

The prose supplier, customer and competitor sections are replaced by the named watch table. The slide deck adds one table slide of escalating and elevated items after the summary slides. The Markdown preview carries the same tables.

## 2. Events and bands

The week's kept signals are grouped by the nightly index's events (spec 42); `grit agents` runs the clustering first. An event is **material** when it is RED, tier 1, or tier 2 with a risk linkage and either two or more independent publishers or a direct or counterparty link.

| Band | Rule |
|---|---|
| ▲ | A material event that is new since the previous report, or that touches a risk whose trend this run is Escalating |
| = | Any other event with a report this week |
| ▼ | A material event of the previous report with no report this week |

An **elevated** event is a ▲ event, or a continuing material event that is tier 1 or RED. The escalating-items table shows up to 8 elevated events. Risk linkage uses the register's numbering: `URS-KEY-05` is "Key Risk 5", `URS-TIER_2-07` is "Tier-2 Risk 7".

## 3. The EventAssessor

For each elevated event, `grit agents` asks the EventAssessor (prompt `event-row-1`) for an item label, a summary, implications for the business area, and a suggested response with an owner. The row is kept only when it passes these checks; otherwise it is withheld and logged in the QA log, and the table shows the event without a draft.

- Every figure appears in a cited source, or in a source's publication date.
- The summary states a date, and a figure when its sources give one (specificity).
- The implications name a line of business, entity, customer, supplier or country, and read as a possibility (could, may or might).
- The response starts with "Consider", names one of the owner roles, and does not say must.
- No ellipsis, no governed score word, no URL.

On 30 October 2026, the first run withheld 9 of 16 rows because the model gave the publication date, which the number check did not yet accept. With source dates counted as grounded, all 15 URS and DS&C rows passed.

The Assessor's prompt (`reader-assessor-6`) also asks for dates, actors, places and figures in each theme summary.

## 4. Look-ahead

A date is kept only when a source linked to the business area states it as a day and a month within the next 30 days. The row is the source's own sentence. Two sentences on the same date are one row when they share most words or come from the same event. Nothing is forecast.

## 5. Coverage, jurisdictions and the quality gate

- A coverage cell is a finding (up to two titles), "No item kept (searched)" when a query of that category returned pages this week, or "Not searched this week". A query that returned nothing is not taken as evidence of a search.
- A signal's line of business comes from its LOB link, a strongly named entity, customer or supplier with a stated LOB, or a competitor row; otherwise it is business-area wide. Its category comes from its rung, a named supplier, or the name of the risk it hits.
- A jurisdiction is any country where the register holds an entity (country of incorporation or asset country). Its finding is the best kept signal that names the country in its title or place links.
- The self-challenge gate answers seven questions by rule: macro checked, every LOB swept, competitors checked, export controls and supply chain checked, every nil searched, named suppliers and customers searched, sources in conflict.
- A conflict is two members of one event that give different counts for the same thing (killed, injured, flights, missiles and similar).

## 6. Source tiers

| Tier | Rule |
|---|---|
| T1 | Official or primary: a government, military or international-body domain |
| T2 | An outlet rated HIGH in the pack's register or the shared ratings |
| T3 | An outlet rated MEDIUM or LOW |
| T4 | An aggregator, or an unrated outlet |

## 7. Scenarios for every business area

`grit scenarios` now drafts best, medium and worst cases for a business-area pack's key risks, as it does for the Group pack's shifts. `grit scenarios-approve` makes the reviewed draft the pack's register, and every brief whose pack has a register shows its outlook and the leading indicators.

On 30 October 2026 the URS (5 key risks) and DS&C (6) registers were recorded as approved by Jony Ling on instruction, for the POC, with BA review pending. The CA draft had evidence for one key risk only, because its 29 October run fell back to Currents; CA was re-scanned with Brave and redrafted.

## 8. Not built

- Internal facts (staff locations, safety confirmations, EBIT bands, RSC dates) from a business partner's weekly notes.
- Probabilities or odds. Scenarios show signposts seen.
