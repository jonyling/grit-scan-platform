# 49 Business-area weekly layout

Status: built on 20 November 2026. This spec records Wilson's layout of 19 November 2026
(`docs/GRIT-Scan_urs_wilson_2026-11-19_May.docx`). It replaces [spec 44](44_Brief_Format_v2.md) for every business-area
and entity pack: URS, URS Taiwan, DS&C, CA and Marine. The Group (STE) radar keeps the spec 44 layout.

## 1. What the reader sees

The weekly business-area brief has a summary, four event tables and two appendices. Every reader of a pack gets the same
layout. A profile still chooses the packs, the aperture and the review role, but it no longer chooses sections.

| Order | Part | Content |
|---|---|---|
| Header | Masthead | Title, scan window, review status and the AI-suggestion notice |
| Box | `SUMMARY` | The Reporter's summary of the week's rows |
| Block | `Exposure & Ask` | Approved country/entity exposure, the dated principal ask, RED triggers, stale warning and uncapped Tier-1 physical threats |
| 1 | Management Focus Topics | Events that answer an active management request |
| 2a | Key, T2 & Emerging | Events that touch the business area's own register risks |
| 2b | Other Risk Themes | Events that touch a Group risk and no register risk |
| 3 | Trends/Developments | Sector, competitor and technology news |
| Appendix A | 1 to 4 | The 30-day look-ahead, the scenario outlook, jurisdiction coverage and the named watch |
| Appendix B | Source Register | Every cited source as `[S#]`, with outlet, title, date, tier and link |

The four tables share one set of columns:

| Column | Content |
|---|---|
| `#` | The row number within the table |
| Item and Risk | The band (▲ or =), the item label, and the risk in brackets |
| Summary | What happened, with dates and figures, then the dated steps |
| Potential Impact/Opportunities | What it could mean for the business area. An opportunity follows when a source shows one. |
| Proposed Response/Management Consideration (Owner) | A "Consider ..." sentence, then the owner role |
| Sources | The row's `[S#]` marks |

The risk label depends on the table:

- Management Focus: `[MF: request title]`.
- Key, T2 & Emerging: the register label, such as `[Key Risk 4]`.
- Other Risk Themes: `[Group: risk name]`.
- Trends/Developments: the register label, or `[Trend]`.

An empty table keeps its header and says "No item this week."

The named watch lists each supplier, customer and competitor with item-specific news. Its columns are Name, Type, This
week, and Proposed Response from Lessons Learnt.

A business-area pack writes Word, PowerPoint and Excel files. It writes no Markdown file and no JSON file. The dashboard
renders its Markdown preview from the brief model. The frozen report keeps the model in `weekly_reports.brief_json`.

## 2. How an event finds its table

Each event goes to one table: the first one it qualifies for, in this order.

1. Management Focus: a member signal answers an active management request of the pack. The match comes from the reader
   route or from the keyword rule recorded at assessment.
2. Trends/Developments: competitor, sector or technology news. No member names the business area's own exposure (a
   `DIRECT` or `COUNTERPARTY` link). A member is a `COMPETITOR` or `SECTOR` report, or every risk the event touches is a
   market risk, such as Competition or Emerging technologies. The row keeps its register label.
3. Key, T2 & Emerging: a member has a strong hit on a register risk or a standing condition.
4. Other Risk Themes: a member's risk cascade reaches a Group risk.
5. Trends/Developments: anything else.

A reasoned review keeps only signals with a register anchor, so rule 2 is what fills the Trends table in a reasoned run.

`grit.pipeline.events.section_of` holds this rule. Each table holds at most 10 rows (`MAX_SECTION_ROWS`). Events keep the
order of spec 44: ▲ first, then the reviewed score or the tier, then the number of publishers. An event without a report
this week has no row.

## 3. Drafting and checks

The EventAssessor (prompt `event-row-4`) drafts one row per event. That is up to 40 calls a pack. It receives the table
name as `section`. For a Management Focus row, the summary says what the news tells management about the request.

The checks are those of spec 44, section 3, with one addition. An opportunity must cite its sources, name a line of
business, entity, customer, supplier or country, and use could, may or might. A failed row is withheld and logged in the
QA log. The QA log is not rendered. The release check still uses it to stop a withheld date or figure from reappearing.

The named-item Reporter (`reader-reporter-6`) also drafts the lessons-learnt response. It is one sentence that starts
with "Consider". It must not say or imply that ST Engineering has the weakness the news describes. The pattern comes from
the adverse scan (`grit.adverse.checks`). A lesson that fails its checks shows "Not drafted." The item's own entry stands.

The Reporter writes the summary box from the drafted rows, the named items and the RED drafts.

## 4. What no longer runs for a business area

The per-risk Assessor, Critic and Options Proposer run for the Group radar only. The finding-card loop does too. A
business-area run stores no condition assessments.

The business-area brief has no legacy what-changed table, prioritisation, coverage matrix, completeness gate, action
posture, corrections ledger, entity packs, priority findings or QA appendix. Its approved internal exposure and one
typed, dated principal ask are shown in the `Exposure & Ask` block after the summary. The Group radar keeps its format-v2
sections.

In a reasoned run (spec 45), Reporting edits `bottom_line.text` only. The final Critic reviews the new brief model.
Readiness does not use the completeness gate, because a business-area brief has none.

## 5. Exposure & Ask governance

The approved exposure document supplies one typed `Exposure & Ask` block. Its table shows country, STE entity,
pack-only staff count and scope, snapshot date, last safety confirmation and status. Staff rows for one business area
never use another business area's counts.

The principal ask is data, not report prose. It names a known reviewer or BA-head profile for the pack, uses an explicit
due date and lists the RED trigger conditions. The admission step rejects an unknown owner, a reader-only owner, an
owner outside the pack and a missing or invalid due date.

An armed strike, missile or drone attack, shelling, bombing or another physical threat in a registered entity or
pack-staff jurisdiction forces the item to Tier 1 and `Escalating`. The block lists every such kept item. The event-row
limit does not limit this list.

ReviewGate holds `APPROVE` with reason `EXPOSURE_DATA_STALE` when a kept Tier-1 physical threat exists and the frozen
exposure snapshot is missing, invalid or more than seven days old at the report run date. The report always labels the
snapshot date. It never presents an older date as current.

Country retrieval uses the versioned `COUNTRY_RISK` register. Qatar, Kuwait, Bahrain and Israel are mandatory URS
searches. Other configured country searches follow registered entity or asset footprints. A jurisdiction may say
`No item found` only after a relevant search was answered. A planned, failed or unrun query gives `Search not run`.

## 5. Rendering an older run

Rows drafted before 20 November 2026 have no table. Run `grit agents` for the pack and date again before you render the
run in this layout.
