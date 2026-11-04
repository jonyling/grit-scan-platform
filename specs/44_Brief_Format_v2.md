# 44 Brief format v2: Wilson's BA-wide layout

Status: built on 30 October 2026. This spec records requests 1 to 5 of 30 October 2026 and the plan in [Wilson-Format-Gap-and-Plan_2026-10-30.md](../docs/progress/Wilson-Format-Gap-and-Plan_2026-10-30.md). Request 6, internal facts from a business partner's notes, is not built.

The opt-in [weekly reasoning extension](45_Weekly_Agent_Reasoning_and_Critique.md)
retains this layout. A named reviewer can supply specific internal evidence;
there is no automatic notes, mailbox, Teams, or travel ingestion. Reporting
and the final Critic operate on the complete report model. Rendering uses the
frozen snapshot and makes no provider call. Earlier-context and hindsight
citations retain their real source dates and display the exception.

## 1. What changes

The weekly brief follows the order of the URS methodology (section 15) and of Wilson's report *URS GRIT Scan, BA-wide, 22–29 October 2026 (v2)*. It is event-led: the week's news is grouped into events, each mapped back to the register risks it touches. The risk-theme table stays as the risk view.

| Order | Section | Source |
|---|---|---|
| Header | `STATUS: STABLE`, `ELEVATED` or `RED`, a ▲ = ▼ arrow per event, and the next dated items | `grit/report/format_v2.py` |
| Box | Bottom line, ending with the number of the action posture section | Reporter |
| 1 | Exposure snapshot: the approved internal snapshot, or a statement that there was none | `grit/pipeline/exposure_snapshot.py` |
| 2 | What changed since the last report | Rule |
| 3 | Look-ahead: the next 30 days, and the leading indicators | `grit/report/lookahead.py` |
| 4 | Escalating and elevated items: item and risk, summary, implications, suggested response with owner, sources | `grit/pipeline/events.py`, EventAssessor |
| 5 | Prioritisation: item, trend, risk linkage, owner or forum | Rule |
| 6 | Coverage matrix: line of business × Operational, Financial / macro, Supply / cost, Competitor / tech, Regulatory / legal | Rule |
| 7 | Jurisdiction coverage: each country with registered entities or assets | Rule |
| 8 onwards | The profile's sections: risk themes, scenarios, management requests, entity packs, priority findings, open watchpoints | Unchanged |
| Then | Named supplier, customer and competitor watch | Reporter's item entries |
| Then | Recommended action posture: the principal ask, when the status becomes RED, each RED candidate with the R&A decision, and the follow-ups by owner | Reporter's ask; rule |
| Then | Quality and completeness check: the self-challenge gate, conflicts between sources, and the corrections ledger | Rule; R&A's ledger |
| Then | Source register: every cited source as `[S#]`, with outlet, title, date, tier and link | Word renderer |
| Appendix | Audit material from the profile: entities by line of business, other signals, and the QA and correction log | Unchanged |

The prose supplier, customer and competitor sections are replaced by the named watch table. The slide deck adds one table slide of escalating and elevated items after the summary slides. The Markdown preview carries the same tables, without the source register.

The scan is wide and the report narrow (URS methodology, section 24), so audit material goes to the appendix. The action
posture follows the methodology's "one major ask, followed by limited follow-ups": the principal ask is the Reporter's
validated escalation text, and the follow-ups list each owner with the escalating items and standing items whose rows suggest
them a response. A watchpoints section appears only when a watchpoint is open.

The corrections ledger (URS methodology, section 22) holds material corrections that R&A records with `grit corrections add`:
the earlier statement, why it was wrong or incomplete, the corrected fact, its source, and the earlier reports to update.
Every later report of the pack carries the open corrections until R&A closes them, and every agent receives them with the
admitted Memory corrections. A correction can retire an exact phrase, such as "S$600m": a Reporting edit that uses it fails
its contract and is repaired, and `grit scorecard` flags a report whose narrative uses it.

## 2. Events and bands

The week's kept signals are grouped by the nightly index's events (spec 42); `grit agents` runs the clustering first. A RED event needs a member signal whose impact the Risk & Assurance operator confirmed (spec 45). A RED candidate that awaits that decision counts as material but not RED; a candidate the operator rejected counts as neither. An event is **material** when it is RED or an undecided RED candidate, tier 1, or tier 2 with a risk linkage and either two or more independent publishers or a direct or counterparty link.

| Band | Rule |
|---|---|
| ▲ | A material event that is new since the previous report, or that touches a risk whose trend this run is Escalating |
| = | Any other event with a report this week |
| ▼ | A material event of the previous report with no report this week |

An **elevated** event is a ▲ event, or a continuing material event that is tier 1 or RED. The escalating-items table shows up to 8 elevated events.

In a reasoned run the reviewed selection decides instead (URS methodology, section 13). An event is material and elevated when a
member has a significance score of 8 or more, an immediate threat, or RED impact confirmed by R&A. Events are ordered by score
within each band, and the best-sourced report leads. A selection that cites another candidate as the same event joins the two,
but only when the two headlines share a distinctive word. Within a week, each elevated event joins the highest-scored story
it matches directly: two or more shared distinctive headline words (`grit.core.headlines`, where "Saudi Arabia" counts as
one word), or the same jurisdiction where the business has entities together with a shared risk. A page whose headline is
too poor to compare is read by its opening. Matches never chain, the best-sourced matching report leads, and the story
keeps its three most common risks.
"No report this week" is not easing: the changes table says "No new report" without an arrow, and the status line names only ▲
events and standing risks whose trend eased. Status-line text ends at a sentence, or at a word with an ellipsis.

A management request that this pack's signals do not answer is matched against the kept signals of the child packs it covers
for the same date, such as URS Taiwan; each match names the pack it came from. The named-item watch leaves out items with only
a passing mention, and the item Reporter reports only news that touches the item's role for the business area. Risk linkage uses the register's numbering: `URS-KEY-05` is "Key Risk 5", `URS-TIER_2-07` is "Tier-2 Risk 7".

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

A wire dateline, such as "Riyadh, November 3 (ANI):" or "WASHINGTON, Nov 3 (Reuters) -", dates the report, not an event. Its date is never read as an event date, here or in the event dates the agents receive, and the row drops the dateline from the sentence.

## 5. Coverage, jurisdictions and the quality gate

- Every planned search that Brave or Currents answers is written to the `searches` ledger, including a search that returned no pages. A search that failed (any status other than 200) is not recorded, because it is not evidence of a nil. Runs from before the ledger fall back to their sightings, and `acquire` backfills the ledger from `searches.jsonl` when a run resumes. Countersearches by the reasoned Critic are not part of the ledger.
- A coverage cell is one of these:
  - A finding (up to two titles).
  - "No item kept (searched)" when an answered search of that category was aimed at that line of business, or the row is the business-area-wide row.
  - "No item kept (business-area-wide search only)" when searches of that category ran, but none was aimed at that line of business.
  - "Not searched this week" when no search of that category was answered.
- A search is aimed at a line of business when its plan row states one (`canonical_lob`), or when its target is an entity, customer, supplier or competitor row with a stated line of business.
- A signal's line of business comes from its LOB link, a strongly named entity, customer or supplier with a stated LOB, or a competitor row; otherwise it is business-area wide. Its category comes from its rung, a named supplier, or the name of the risk it hits.
- A jurisdiction is any country where the register holds an entity (country of incorporation or asset country). Its finding is the best kept signal that names the country in its title or place links. A jurisdiction with no finding shows "No item found in the checked sources." only when an answered search targeted one of its entities or named the country or an alias. Otherwise it shows "No item found; no country-specific search ran this week."
- The completeness gate answers eight questions by rule. Five are critical: macro and financial items checked, every line of business searched, competitors checked, export controls and the supply chain checked, and every jurisdiction searched. Three are not critical: every nil backed by a search of its own scope, named suppliers and customers searched, and sources in conflict.
- A critical question answered "No" blocks release. The brief records the blocking questions and every other limitation under `method.weekly.completeness`, and a reasoned weekly report with a blocker is `INCOMPLETE` even when the Critic raises no issue. The rendered gate marks each critical question and states when the brief is not ready for release.
- A risk's category comes from words in its name. "Economic slowdown" and "recession" count as financial and macro, so a macro risk named that way satisfies the financial check.
- A conflict is two members of one event that give different counts for the same thing (killed, injured, flights, missiles and similar).

## 6. Source tiers

| Tier | Rule |
|---|---|
| T1 | Official or primary: a government, military or international-body domain, or a register source of that class |
| T2 | An outlet rated HIGH in the pack's register or the shared ratings |
| T3 | An outlet rated MEDIUM or LOW |
| UNRATED | An outlet R&A has not banded yet; its reports need verification |
| T4 | An aggregator, tracker or prediction market |
| CONTESTED | An outlet on R&A's contested list; never the sole support for a fact |

`grit.core.source_bands` holds these rules for the pipeline and every renderer.
The quality check adds a row naming the cited outlets that have no R&A band,
so R&A knows what to band next.

## 7. Scenarios for every business area

`grit scenarios` now drafts best, medium and worst cases for a business-area pack's key risks, as it does for the Group pack's shifts. `grit scenarios-approve` makes the reviewed draft the pack's register, and every brief whose pack has a register shows its outlook and the leading indicators.

On 30 October 2026 the URS (5 key risks) and DS&C (6) registers were recorded as approved by Jony Ling on instruction, for the POC, with BA review pending. CA's 29 October run fell back to Currents and gave evidence for one key risk only. CA was scanned again with Brave for 30 October (131 queries, 369 pages kept); its register covers 6 of its 8 key risks, recorded in the same way. Key risks 4 and 5 had no evidence that week.

A package already pending review is not replaced by a new layout: a reviewer returns it for rework first. Previews in format v2 for Wilson are in `output/runs/RUN/preview-format-v2/`.

## 8. Signpost Judge (added 2 November 2026)

The first business-area briefs showed "No signpost seen" for every risk, although each risk had 9 to 49 relevant signals. The tracker matched signposts by keyword phrases written for a 1 to 5 year horizon, such as "multi-protocol electronic tolling", and needed two of them in one report. Weekly news rarely uses those phrases.

`grit agents` now runs the Signpost Judge after the escalating-items rows:

1. For each signpost and counter-indicator, the signals that touch the risk are ranked by embedding similarity to its text. The closest 3 with cosine 0.60 or more are candidates.
2. The Signpost Judge (`signpost-judge-1`) names the candidates that report the development itself, not just the subject, each with a verbatim quote.
3. A match is kept only when the report was a candidate for that item, the quote is verbatim, and the quote shares at least one specific word stem with the item's text (`OFF_TOPIC` otherwise).
4. Kept matches are stored on the run (`signposts_seen`, `signpost_quotes`) and count as seen with keyword matches. The scenario table prints the quote as the evidence.

On 2 November 2026: URS 1 match (a report of a Rs 4.4 trillion infrastructure cost overrun, URS Key Risk 3, worst case), CA 3, DS&C none. A judged match without a shared specific word, such as an airport's unleaded-fuel campaign offered for a Defense Production Act signpost, is dropped.

## 9. Not built

- Internal facts (staff locations, safety confirmations, EBIT bands, RSC dates) from a business partner's weekly notes.
- Probabilities or odds. Scenarios show signposts seen.
