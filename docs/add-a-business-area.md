# Add a business area

This guide adds a business-area (BA) reader pack, such as Commercial Aerospace or Defence & Public Security, alongside Urban Solutions. A new pack is a data change: a folder of registers under `config/`, a scan plan, and reader profiles. No pipeline code changes.

The Urban Solutions pack (`config/urs/`) is the worked example throughout. Copy its files and change the content.

## Before you start

You need:

- The BA's Entity Info workbook in the standard template: sheets `2. Entity Address`, `3. Competitors`, `4. Customers`, `5. Suppliers`, `6. LOB_Keywords`, `7. Annual Risks`, `9. Specific Industry News`, `10. Management Focus`, `News-Pri`, and `News-Sec`, plus the hidden LOB keyword sheets.
- The BA's org chart, to write its LOB hierarchy by hand.
- The names of the BA head, the R&A business partner, and anyone else who reads the BA brief.
- A short pack id, such as `cas`, and an id prefix, such as `CAS`.

## Create the pack

1. Copy the workbook to `secrets/`.

2. Create the folder `config/PACK/`, where `PACK` is the pack id.

3. Write `config/PACK/pack.json`. Copy `config/urs/pack.json` and change these fields:

   | Field | Value |
   |---|---|
   | `pack` | The pack id. It must match the folder name. |
   | `level` | `BA`. |
   | `label`, `shortLabel`, `businessArea` | The BA's name, its abbreviation, and the name used on sheet 2. |
   | `children` | Entity packs under this BA, such as `["cas-us"]`. Use `[]` if there are none yet. |
   | `registers`, `optional` | The register file names. Use the prefix, such as `CAS-STE-Themes.json`. |
   | `build.prefix` | The id prefix, such as `CAS`. Theme ids become `CAS-KEY-01`. |
   | `build.defaultWorkbook` | The workbook's file name in `secrets/`. |
   | `build.keywordSheets` | The names of the BA's hidden LOB keyword sheets. |
   | `build.lobHierarchy` | The hand-authored hierarchy file, such as `CAS-LOB-Hierarchy.json`. |

4. Write the LOB hierarchy by hand from the org chart. Copy `config/urs/URS-LOB-Hierarchy.json`: each LOB has a name, its grouping, and the other labels the workbook uses for it. Set `status` to say a BA owner must validate it.

5. Build the generated registers from the workbook.

   ```bash
   .venv/bin/python -m grit pack-build --pack PACK --workbook secrets/WORKBOOK.xlsx
   .venv/bin/python -m grit pack-show --pack PACK
   ```

   Read the counts. A row that a parser could not place is listed under `dropped` with a reason. Fix the workbook, not the JSON.

6. Create an empty `config/PACK/PREFIX-Relationships.json` with `{"registerId": "PREFIX-RELATIONSHIPS", "status": "NO_ROWS", "edges": [], "aliases": []}`. The BA's answers to the graph's gaps go here later.

## Write the scan plan

The scan plan decides what is searched every week and what the brief calls its sections. Copy `config/urs/URS-Scan-Plan.json` to `config/PACK/PREFIX-Scan-Plan.json`, and add `"scan_plan": "PREFIX-Scan-Plan.json"` to `optional` in `pack.json`.

1. **Themes.** Keep `"standingSet": "themes"`. For each theme id in the generated themes register, write two or three queries anchored to the BA's own sectors. "Rail cyberattack" finds what matters to URS; "cybersecurity" finds everything. A theme an entity pack covers, such as a country-concentration risk, gets `"coveredBy": "ENTITY-PACK"` and no queries.

2. **Management requests.** Copy each numbered request and each focus item from sheet 10. Give each one:
   - `status`: `ACTIVE` for a numbered request, or a focus item marked "Management priority" or "Core control focus"; `PROPOSED` for one marked "Research draft".
   - `queries`: one to five searches.
   - `keywords`: the phrases that make a signal P1. Join parts with ` + ` when all of them must appear, such as `Qatar + work visa`. A keyword with no country or sector in it will match news from everywhere.

3. **Named items.** Keep `"namedItems": {"source": "graph"}`. Set each lane's `cadenceWeeks` so the weekly total stays inside the Brave budget. Set `genericNames.cues` to the places where the BA's public-body customers sit, so "Ministry of Defence" counts only for the right country.

4. **Section titles and audience.** Rename sections in `sectionTitles`, and name the readers in `audience`. The bottom-line agent writes for them.

5. Print the plan and check the weekly count.

   ```bash
   .venv/bin/python -m grit plan --pack PACK --list 200
   ```

   The Brave plan allows about 2,000 queries a month across all packs. On 2026-10-07, URS used 118 a week and URS Taiwan 60.

## Add the readers

Add a brief for the new pack to each reader's profile in `config/profiles/`. A profile can read several packs. Add the pack to `packs`, then add an entry under `briefs`, keyed by the pack id, with its title, sections, and formats. Copy the `urs` entries in `wilson.json`, `hwaimay.json`, and `gareth.json`.

Give the BA head `"reviewRole": "BA_HEAD"`. Only the BA head's approvals go to AIRIS.

## Run and review the first week

1. Check the queries without spending any.

   ```bash
   .venv/bin/python -m grit weekly --pack PACK --dry-run
   ```

2. Run the week. If the BA has entity packs, run them in the same command, so the BA brief can summarise them.

   ```bash
   .venv/bin/python -m grit weekly --pack ENTITY-PACK PACK
   ```

3. Read the brief with the BA's R&A business partner. Tune the scan plan's queries and request keywords, then rerun `grit assess` and `grit agents`. Assessment costs no search.

4. Export the alias questions for the BA, and import their answers before the next week.

   ```bash
   .venv/bin/python -m grit gaps export
   ```

## Known limits

- `grit gaps` and the `grit digest-*` commands read the `urs` pack today. Add a pack option before you use them for another BA.
- The harm scorer's transmission registry reads one workbook (`GRIT_WORKBOOK`). A second BA needs its own registry or a merged workbook.
- The general theme vocabulary in `grit/ontology/build_pack.py` is keyed by the start of each theme's name. A BA whose sheet 7 uses new theme names keeps the title only, until someone adds vocabulary for it.
