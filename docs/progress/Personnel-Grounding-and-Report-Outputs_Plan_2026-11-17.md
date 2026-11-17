# Personnel grounding and report outputs

**Date:** 17 November 2026 (Asia/Singapore)  
**Status:** Plan only. Not built.  
**Origin:** Request captured on the Cloud Agent environment-setup chat; filed here so the grit-scan-platform coding project can pick it up.

## Requests

1. Use `secrets/adverse/internal-context.json` to ground **all** signals and reports to STE personnel in those countries as a priority — not only the adverse report.
2. Produce the adverse report as a **Word** document, and the signals as a **CSV** file.
3. **Standardize** table headers for all Word reports.
4. When signals are loaded to **SharePoint**, add a column that names a signal **Adverse** when it is deemed adverse.

## Where we are today

| Area | Current behaviour |
|---|---|
| People grounding | Only `grit adverse`, via `grit/adverse/people.py` → `secrets/adverse/internal-context.json` (spec 46). |
| Adverse outputs | `email.txt` / `.eml`, `email.md`, `review.md`, `report.json`. No Word, no CSV. |
| Word tables | Weekly: navy `Table Grid` in `grit/report/docx_brief.py`. Daily review: `Light Grid Accent 1` in `grit/review/pack.py`. WSH: plain cells in `grit/wsh/digest.py`. Adverse: none. |
| Signals CSV | Daily lane only (`grit/daily/lane.py` `CSV_COLUMNS`). |
| SharePoint | Daily review lists only (`grit/review/sharepoint.py` `COLUMNS`). No Adverse column. |

## Source of truth for people

Use the R&A layout already defined for adverse (and mirrored in the uploaded summary):

- `middle_east_local_staff` — `as_at` + `entries` of `{entity, country, headcount}`
- `middle_east_travellers_and_assignees` — `as_at` + `by_country`
- `entity_level_examples` — `{entity, country, staff}`

Rules that stay:

- Country level only; no invented city or site from this file.
- Data older than 30 days is still used and labelled stale.
- Entity registered countries in workbooks are **not** staff presence. The staff log is the only source for people exposure.
- Agents propose; deterministic functions decide; a named human approves. Priority means ranking and disclosure, not inventing a controlled score.

## Delivery sequence

### P0 — Shared people module

Lift `grit/adverse/people.py` into a shared module (for example `grit/core/people.py` or `grit/ontology/people.py`). Adverse keeps a thin re-export. Every lane that scores or briefs a page calls the same country matcher against the staff log.

### P1 — Priority grounding (all lanes)

| Lane | Change |
|---|---|
| Adverse (existing) | Keep `PEOPLE` rung, A5 exposure, staff lines, Final Validation. |
| Weekly BA / Group | If a page matches a staffed country and safety/security or people-risk terms: elevate within tier; append `relevance_basis` with the staff summary and as-at; never claim Direct exposure from people alone. |
| Daily lane / review | Same match → priority affinity and “Why kept” / Relevance text; feed SharePoint Adverse when the classifier says Yes. |
| WSH digest | Prefer incidents in staffed countries in ordering / alert notes; cite the staff log when used. |

Keep the `PEOPLE` rung **adverse-only**. Weekly and daily keep the BA link ladder (`DIRECT` / `COUNTERPARTY` / …) and use priority + basis text instead of a new rung.

### P2 — Adverse Word + CSV

Extend `grit adverse` so a READY run also writes:

| Artifact | Content |
|---|---|
| `output/adverse/DATE/adverse.docx` | Same sections as the email: Immediate Escalation, A/B/C tables, Management Attention, Final Validation (including the staff-log validation line). |
| `output/adverse/DATE/adverse-signals.csv` | One row per included item or source page: section, severity, action, country, staffed?, Adverse=Yes, URL, publisher, why_adverse, exposure_basis. |

Keep `email.md` / `.eml` for paste-to-Wilson. Reuse the shared Word table helper from P3.

### P3 — Standardize Word table headers

Introduce `grit/report/table_style.py`: one navy header cell style (today’s weekly look) and named header tuples. All Word writers call it.

| Report | Header set |
|---|---|
| Weekly brief | Gold standard: News/Developments, News/Information Source, Date and Time, Relevance/Potential Impact… |
| Daily review pack | Same chrome; align labels where semantics match; document intentional column differences. |
| WSH digest | Same header chrome; keep WSH columns unless product asks to rename. |
| Adverse Word (new) | Section tables styled identically (Event / Sources, date and time / Why adverse & impact / Severity & action). |

Unify **style** first. Renaming review or WSH column wording needs explicit sign-off (Wilson / Jony).

### P4 — SharePoint Adverse column

1. Add `Adverse` (Yes/No) to `grit/review/sharepoint.py` `COLUMNS` and `row_fields()`.
2. Update `docs/daily-review-setup.md` so the list owner creates the column once on each list.
3. Publish the field with each daily-review row.
4. Optional ops view: filter Adverse = Yes.

## Open decisions (recommended defaults)

| Question | Recommended default |
|---|---|
| When is a BA/daily signal “Adverse”? | Yes when (a) the same URL/signal appears in the latest READY `adverse_items` as included, or (b) people-risk lexicon + staffed country match. Prefer (a)+(b) first; avoid model-only labels. |
| BA ladder vs PEOPLE | Keep PEOPLE adverse-only; boost priority and cite the staff log for weekly/daily. |
| SharePoint schema | Code publishes; site owner creates the Yes/No column once per list. |

## Primary touchpoints

| File / area | Role |
|---|---|
| `grit/adverse/people.py` → shared people module | `load_people`, country match, staff line |
| Weekly / daily assessment composers | Staffed-country priority bump + basis text |
| `grit/adverse/render.py` + new adverse docx/CSV | Word pack and signals CSV |
| `grit/report/table_style.py` (new) | Shared header chrome |
| `grit/report/docx_brief.py`, `grit/review/pack.py`, `grit/wsh/digest.py` | Consume shared style |
| `grit/review/sharepoint.py`, `docs/daily-review-setup.md` | Adverse column |
| Specs 46, and short addenda on 42 / 44 / 48 | Document the cross-lane behaviour |
| `tests/test_adverse.py`, `test_daily.py`, `test_daily_review.py`, `test_format_v2.py`, new people tests | Contract locks; fixtures supply staff JSON (no live `secrets/` in tests) |

## Acceptance checks

1. With `internal-context.json` present, a Qatar / UAE / Israel safety headline is prioritised and cites the staff log in weekly and daily outputs, not only adverse.
2. `grit adverse` writes `.docx` and `.csv`; the email path is unchanged.
3. Weekly, daily-review, WSH, and adverse Word tables share one header chrome.
4. SharePoint publish includes `Adverse`; Yes when the classifier fires; tests lock `COLUMNS` and CSV.
5. Tests never read live `secrets/`; fixtures supply the staff JSON.

## Next step

Implement on a feature branch from this plan, starting at P0 → P1, then P2–P4. Confirm the Adverse classifier and any column-renaming scope before large header wording changes.
