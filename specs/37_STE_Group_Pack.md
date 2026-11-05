# 37. The STE Group pack

**Status:** Draft for review, 2026-10-13. Extends specs 35 and 36.
**Trigger:** Wilson's email of 9 October 2026 and the R&A team's decisions of 13 October 2026.

The Group pack (`ste`) gives the CEO and the Chairman the geopolitical and technology picture facing ST Engineering as a whole. It sits above the business-area packs and does not replace them. It is a condition pack, like URS Taiwan: Wilson's 12 dashboard themes are its standing conditions, measured each month against a baseline that Wilson signs off.

## 1. Decisions

| # | Decision |
|---|---|
| 1 | Readers: Vincent S. Chong (CEO) and MK Teo (Chairman). Both are readers only. The Chairman's views reach the pack through Wilson. Wilson reviews and approves each release. |
| 2 | The executive brief is monthly. The scan window is 31 days. |
| 3 | The strategic shifts are Wilson's eight. |
| 4 | GRIT-Scan drafts the 12 baselines from cited evidence. Wilson signs them off. |
| 5 | The model proposes scenarios. A person reviews and edits them before they are used. |
| 6 | The Group pack uses a looser link to STE: the footprint rung. |
| 7 | Currents backs up Brave when Brave stops or returns too little. |

## 2. Registers

`grit pack-init --pack ste` writes the pack once. It never overwrites a file. After that, the files under `config/ste/` are the source.

| Register | Content |
|---|---|
| `pack.json` | Level `GROUP`. Its children are the business-area packs whose registers make up the footprint. |
| `STE-Global-Baseline.json` | The 13 standing conditions: name, domain, how each reaches STE, its watch indicators, and its `currentAssessment`. The assessment is empty and the status is `DRAFT_PENDING_SIGN_OFF` until Wilson signs off the baseline draft. |
| `STE-Group-Themes.json` | Wilson's eight strategic shifts, as `KEY` themes with keywords. A condition's development is tagged with the shifts it touches. |
| `STE-Scan-Plan.json` | Queries, keywords, and proposed business-area relevance for each condition. A 31-day window. |
| `STE-Source-Register.json` | Wilson's tier 1 to 7 sources, rated. Primary and official sources are `HIGH`. |
| `STE-Exposure-Ontology.json`, `STE-LOB-Profiles.json` | Empty. The Group pack derives its footprint from its children at run time. |

## 3. The footprint rung

A Group pack builds one graph from its children's graphs. The footprint is every country where a registered STE entity sits.

`FOOTPRINT` is a new rung, weaker than `SECTOR`. A Group-pack signal is on it when all of these hold:

- it has no stronger link;
- it names a footprint country in the title or lead;
- it strongly hits one of the 13 standing conditions.

Example: "Saudi–Houthi war reaches Riyadh" is on the footprint rung when Saudi Arabia is in the footprint and the signal hits the Middle East security condition. A country alone is still not a link. The rung applies only to Group packs, so the business-area briefs keep their strict link.

A footprint signal has news priority P3.

## 4. What the Assessor writes for a Group condition

For each condition with validated evidence, the Assessor writes these fields. Each one is checked and dropped if it fails; the assessment stands.

| Field | Rule |
|---|---|
| `implication` | The risk pathway: what the development could mean for STE. It names a business area, line of business, entity, or footprint country, uses could, may or might, and cites its sources. |
| `opportunity` | The opportunity pathway, under the same rules. It can be empty when the sources show none. |
| `counter_indicator` | One sentence on what development would show the assessment is wrong. No citation is needed, but no number may be added and no score word used. |

The business areas each condition touches come from the scan plan (`businessAreas`). They are proposed until Wilson confirms them. The Proposer runs for a Group condition that is escalating or cites a material source.

## 5. Baseline draft

`grit baseline-draft --pack ste --date DATE` reads a completed run. For each condition, the Baseliner writes the current position from that run's sources: three to five cited sentences, with quotes and grounded numbers, under the same checks as the Assessor. A condition with no evidence says so; nothing is invented.

The output goes to `output/runs/RUN/baseline-draft/`. It holds a Word document for Wilson and a JSON file with each condition's drafted `currentAssessment` and sources. After Wilson edits and signs off, copy the text into `STE-Global-Baseline.json`, and set `status` to `SIGNED_OFF` with his name and the date.

## 6. Scenarios

Spec 43 replaces this section. For each strategic shift, the Strategist now proposes a best, a medium and a worst case for STE, built on the shift's State of the Situation. `grit scenarios --pack ste --date DATE` drafts them for Wilson's review.

## 7. Monthly Group Strategic Radar

For a Group pack, the standing-items section is one table with a row per condition. The columns are:

- Theme and what changed;
- Sources, with date and time;
- Risk and opportunity for STE;
- Business areas;
- Counter-indicator;
- Proposed response, when the profile includes `RESPONSE_OPTIONS`;
- Priority and trend.

The deck follows the reference format. Its appendix has one slide per condition with validated evidence, escalating first.

## 8. Currents fallback

After Brave, the Researcher sends to Currents every query that Brave did not answer, and every query that returned nothing. That covers a query left unsent by the monthly limit, a rate limit, or a Brave error. The fallback is capped per run by `CURRENTS_FALLBACK_MAX`, which defaults to 40. Each Currents search is logged like a Brave search and marks its query answered, so a resumed run does not repeat it. Without `CURRENTS_API_KEY`, the fallback is skipped and the log says so.

## 9. Limits

- The footprint covers only the business areas with packs: URS and DS&C today.
- Business-area relevance and the source ratings are proposed until Wilson confirms them.
- The baseline is empty until Wilson signs it off. Until then, the monthly assessment compares against the previous month only.
