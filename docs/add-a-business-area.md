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

1. Copy the workbook to `secrets/`. Name it with the BA tag, such as `01_CA_Entity_Info.xlsx`: when the manifest's file name is missing, the newest file carrying `PREFIX_Entity_Info` is used.

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
   | `build.keywordSheets` | The names of the BA's hidden LOB keyword sheets. Use `[]` if it has none. A workbook copied from the URS template can carry the URS sheets. Do not list those. |
   | `build.lobHierarchy` | The hand-authored hierarchy file, such as `CAS-LOB-Hierarchy.json`. |

   A workbook that departs from the URS layout needs these optional `build` fields. The DS&C workbook needs all of them.

   | Field | When the workbook has |
   |---|---|
   | `themeCounts` | A sheet 7 section with a different number of rows, such as `{"EMERGING": 5}`. The default is 6 key, 5 tier-2 and 3 emerging risks. The build fails if a count is wrong. |
   | `themeVocabulary` | Theme names the built-in vocabulary does not know. Key each list by the start of the theme name, such as `"talent retention": ["tech talent", "attrition"]`. |
   | `sourceSheets` | A source sheet with another column layout, such as `{"9. Specific Industry News": {"name_col": 1, "heading_col": null, "numbered": true}}`. Columns count from 0. `numbered` strips a leading "1. " from each name. |
   | `themeActionPlans` | Key risks with an Action Plans column on sheet 7. Set `true` so the Proposer can build on the action plans (spec 36). |
   | `managementFocusLayout` | `"areas"` when sheet 10 is one row per focus area (S/N, area, watchpoints), not numbered requests and a LOB / Scope table. `"scenarios"` when it holds scenario tables (No., Scenario, description, impact or risks, watchpoints), as CA's does. |
   | `entitySheet` | An entity sheet with another name, such as CA's `2a. Entity and Asset Address`. Its header row is found by its NameOfEntity column, so a note line above it is skipped. |
   | `assetSheet` | A finance asset listing, such as CA's `2. Asset Listing (Finance)`. Each entity gets `assetCountries`, which counts as footprint but is never taken as its country of incorporation. |
   | `supplierUnit`, `sharedSupplierSeparator` | Supplier BA values that name two business areas, such as `URS/DS&C`. Set the BA's own label (`DS&C`) and the separator (`/`). The supplier is recorded under the BA, and `sharedWith` names the other. |
   | `themeLayout`, `themeClassificationRequire` | A sheet 7 that is one `Risk Theme \| Rationale \| Classification` table, as Marine's is. Set `themeLayout` to `"classified"`. Each row's tier is the first tier its classification names, so `Key/Tier 2` is Key. Set `themeClassificationRequire` to text that only governed rows carry, such as `"BA Annual Risk Refresh"`. Other rows are listed under `draftThemes` and are not scanned. With this layout, `themeCounts` is checked only for the tiers it names. |
   | `classRows` | Customer or supplier rows that name a class of counterparty, such as `Marine equipment vendors`, not an organisation. Give a regular expression for each sheet, such as `{"customers": "\\bclass\\b", "suppliers": "\\b(suppliers\|OEMs\|vendors)\\b"}`. A matching row stays in the register, marked `counterpartyClass`, and is not searched by name. |

   The build also handles these differences without configuration:
   - A sheet 6 cell with two or more commas is split into one keyword per item.
   - A sheet 6 with a `Keyword | LOB` header, as Marine's has, is read keyword first. A keyword with no LOB is filed under `All LOBs`.
   - An entity sheet with plain headers (`Entity Name`, `LOB Grouping`, `LOB`, `Country`, `Ownership Type`), as Marine's has, is read as the template's NameOfEntity, LOBGrouping1, LOB1, CountryOfIncorporation and NatureOfShareholding. A title line above the header is skipped.
   - The supplier header row is found by its `BA` column, whatever column A says. A sheet with no `BA` column is found by a column A header that starts with `Supplier`, such as Marine's `Supplier Category`.
   - A competitor sheet headed `Region | Competitors/Peers`, as Marine's is, keeps each row's region. The region is not a LOB.
   - A competitor LOB label such as "1. Mission Software & Services" resolves without its number.
   - A customer named only by country and programme, such as "Thailand NPE Programme Customer", is marked `anonymised`. It is not searched by name.
   - A legal name is searched by a shorter name. A branch, such as `ST Engineering Marine Ltd. - Abu Dhabi Branch`, is searched by its parent's name. A country after "of", as in `Ministry of Home Affairs of Singapore`, stays in the name.
   - Customer and supplier sheets are read by their header row. A sheet with a serial column keeps numbered rows; a list without one, such as CA's harmonised customer list, keeps every named row once. A short name (match key) becomes an alias. Country, customer type, sector, primary LOB, workscope, contracting entity, customer group, category, goods or services, concentration flag, linked risk codes and confidence are copied when the sheet has them. The graph links a customer to its stated LOB and contracting entity, and a supplier to its stated LOB. A counterparty's country stays a field: it is not STE footprint.
   - Sheet 7 sections headed only `Ref | Risk` are found by their headings, such as `2. Tier-2 Risks`.
   - On sheet 10, a numbered row whose text is only a topic heading (Opportunities, Challenges, Key Risks) continues the request above it, and a request that lists its countries in its text keeps them. Each such reading is noted on the request.

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

1. **Themes.** Keep `"standingSet": "themes"`. For each theme id in the generated themes register, write queries anchored to the BA's own sectors: about four for each key risk, drawn from its sheet 7 drivers, and one or two for each tier-2 and emerging risk. "Rail cyberattack" finds what matters to URS; "cybersecurity" finds everything. A theme an entity pack covers, such as a country-concentration risk, gets `"coveredBy": "ENTITY-PACK"` and no queries.

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

To show the Proposer's options for management consideration under each key risk, add `RESPONSE_OPTIONS` to the brief's `sections`. Give it to the readers who act on the options, such as the BA head and the R&A business partner.

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

## Add the business area to the Group pack

The Group pack (`ste`) draws its footprint from its business-area children (spec 37). Add the new pack id to `children` in `config/ste/pack.json`, and add it to `businessAreas` for each condition it touches in `config/ste/STE-Scan-Plan.json`.

## Known limits

- `grit gaps` and the `grit digest-*` commands read the `urs` pack today. Add a pack option before you use them for another BA.
- `GRIT_WORKBOOK` overrides only the URS workbook. To point another BA at a different file, set `GRIT_WORKBOOK_PREFIX`, such as `GRIT_WORKBOOK_DSC`.
- The harm scorer reads each pack's own workbook. The reviewed aliases in `config/registry_aliases.json` name URS register ids, so they apply to URS only.
- `config/risk/Risk-Cascade.json` names the URS and URS Taiwan theme registers. A pack outside that lineage uses its own themes at its own level, and no entity level.
- The RED patterns in `config/red-patterns.json` list the packs they apply to. Add the new pack id to each pattern that should cover it.
