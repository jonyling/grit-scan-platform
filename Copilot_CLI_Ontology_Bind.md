# Copilot CLI — Ontology bind engine on existing GRIT-Scan

Read this whole file before writing code. Specs 13–34 and the repository README still govern. This file does not replace them.

## Mission

Add a **deterministic bind engine** on top of the packs and STE register that already exist.

The engine answers one question:

> Does this candidate instantiate a governed pattern **and** touch a named STE entity, customer, supplier, site, line of business, or corridor?

If the STE link is missing, it is **not** a bound GRIT signal. Keep it unclassified. Do not invent the link.

This is not a new product. It is not a vector database. It is not a second harm scorer. It is not Red-alert mail. It is not a weekly PPT writer.

## Product spine (do not fork)

One signal object. Three cadences. One bind.

| Cadence | Bind required | This change may do | This change may not do |
|---|---|---|---|
| Daily / wild scan | Yes, to show as bound | Replace title word-subset theme bind | Score harm, send mail, advance past `NEW` |
| Weekly / `revise()` | Yes | Use the same bind function | Add a Taiwan-only bonus or a second ranker |
| Red | Yes, plus a named Red pattern | Flag `aperture=RED` on the hit | Send email |

Agents propose. Deterministic functions decide. A named human approves.

A missing fact stays missing.

No scoring path calls a language model.

## What already exists — use it

Do not reimplement these.

| Existing | Role |
|---|---|
| `grit_scan/ontology.py` | Pack loader. Taiwan is a reader, not the world. |
| `config/urs/` and `config/urs-tw/` | Registers, baseline conditions, `steThemes`. |
| `secrets/URS Entity Info.xlsx` + `grit_scan/registry_loader.py` + `config/registry_aliases.json` | STE entities and transmission. |
| `grit_scan/matchers/` | Keyword / exposure / LOB / theme binding. Keep; call from the new bind, or replace **one** call site. |
| `grit_scan/impact_scoring.py` | Harm scorer. Do not call it. |
| `grit_scan/relevance_scoring.py` | Weekly briefing labels from ontology fields. Do not create a parallel file. |
| `grit_scan/weekly_pipeline.py` `revise()` / `rank()` | Weekly path. |
| `dataverse/run_wild_scan.py` | Live Currents `NEW` rows. Theme bind is title-only word-subset, hardcoded to URS-TW. **This is the first call site to replace.** |
| `acquisition/` | Candidates. Must not import `assess_impact`. |
| `agents/` | Proposers. May emit triples later. Must not decide theme, risk, or severity. |
| `dataverse/choices.py` | Choice codes. Do not invent columns. |
| Specs 13–33, spec 34 | Rules and rebuild order. |

`urs` baseline conditions are empty. Weekly import still defaults to `urs`. Wild scan ignores `--pack`. Fix pack selection as part of this work if you touch those commands. Do not copy Taiwan conditions into `urs`.

## Direct STE link — non-negotiable

A bind is valid only when **all** of the following hold:

1. A pack is named (`urs` or `urs-tw`, from `--pack` or an explicit caller argument). No silent default to Taiwan on a non-Taiwan run.
2. At least one **register id** from that pack matches. Allowed classes: STE legal entity, customer, supplier, site, line of business (LOB), named corridor / country **only if that country is a register row**, not because “Taiwan” appeared in a headline.
3. Country of incorporation is not a site. A country string without a site or counterparty row is not a STE link.
4. The match type is recorded: `REGISTER_ALIAS` | `REGISTER_EXACT` | `LOB_EXACT` | `LOB_KEYWORD` | `GDELT_ACTOR_MAPPED` | `PROPOSED_TRIPLE_ACCEPTED`.
5. If nothing in (2) hits, result is `UNBOUND`. Theme may still be empty. Do not apply `CONDITION_DEFAULT_FALLBACK` to manufacture a STE link.

Headline text containing a customer name is not enough unless that string maps through `registry_aliases.json` or the pack entity list to a stable id.

An LOB link is enough on its own, because news rarely names the legal entity. It needs exactly one active LOB in the named pack to match by registered name or governed keyword, through `grit_scan/matchers/lob_matcher.py`. A match to more than one LOB is not a link. `priorityCountries` alone is not a link. An LOB-only bind has no entity id: `entity_ids` carries the LOB id and `match_types` records `LOB_EXACT` or `LOB_KEYWORD`.

A `ManagementRequest` (spec 29) is never an STE link. It sets priority for a signal that is already bound. A request keyword extends an LOB's keyword match only after governance sets the request `ACTIVE`, and only when the request is scoped to that LOB. The request keyword field is not stored yet, so `bind_candidate()` ignores requests until it is.

## What to add (small)

```
config/ontology/
  schema.yaml          # classes, relations, constraints — human owned
  patterns.yaml        # theme / condition shapes + optional aperture: DAILY | WEEKLY | RED
  ste_link_rules.yaml  # which register sheets / fields count as STE link (optional if schema covers it)

grit_scan/bind.py      # pure functions: bind_candidate(), fire_patterns()
tests/test_bind.py     # golden cases
```

Optional, only if an existing module is the wrong place:

```
grit_scan/triples.py   # validate proposed triples against schema.yaml
```

Do **not** add: Neo4j, RDFLib, FAISS, a SQLite second brain, new Dataverse tables, a Streamlit ontology explorer, Red mailer, or `ontology_v2.py` that shadows `ontology.py`.

Load schema and patterns the same way packs load: from `config/`, fail closed if the file is missing or invalid.

## `schema.yaml` — keep small

Human written. The model does not invent relations at runtime.

Suggested starting set (edit to match the actual workbook columns; do not invent STE names):

```yaml
version: 1
classes:
  - STE
  - LOB
  - Customer
  - Supplier
  - Site
  - State
  - Org
  - Policy
  - Good
  - Place
  - Event
relations:
  involves:
    from: [Event, Org, State]
    to: [STE, Customer, Supplier, Site, LOB]
  operates_in:
    from: [STE]
    to: [LOB]
  located_in:
    from: [STE, Customer, Supplier, Site, Org]
    to: [Place, Site]
  supplies:
    from: [Supplier, Org]
    to: [STE, Customer]
    obj: [Good]
  restricts_export_to:
    from: [State, Org]
    to: [State, Org]
    obj: [Good, Policy]
  sanctions:
    from: [State]
    to: [State, Org]
    obj: [Policy]
  military_action_toward:
    from: [State]
    to: [Place, State]
goods:
  - semiconductors
  - lithography
  - dual_use_ai
  - rare_earths
```

Drop any relation you cannot map to a register field or a GDELT EventCode table. Empty relations are better than fake ones.

Add `config/ontology/cameo_map.yaml` only if you use GDELT EventCodes. Map a **small** set of codes to relations. Unmapped codes stay unmapped. Do not map the entire CAMEO book.

## `patterns.yaml` — conditions you already govern

Each pattern points at an existing baseline condition id or a named theme id from the pack. Do not create a parallel condition catalogue.

```yaml
version: 1
patterns:
  - id: US_CN_TECH_RIVALRY
    pack: [urs, urs-tw]          # only packs that may fire it
    condition_id: null           # set to a real baseline id when one exists
    aperture: [DAILY, WEEKLY]
    require_ste_link: true
    actors_any: [US, PRC]
    rel_any: [restricts_export_to, sanctions]
    obj_any: [semiconductors, lithography, dual_use_ai]
    exclude_rel: []
  - id: TW_PERSONNEL_DUTY_OF_CARE
    pack: [urs-tw]
    condition_id: null           # MUST be filled from URS-TW baseline; leave unused if no id
    aperture: [DAILY, WEEKLY, RED]
    require_ste_link: true
    require_site_or_personnel: true
    actors_any: [PRC, PLA]
    rel_any: [military_action_toward]
    places_any: [TAIWAN]
```

Rules:

- `require_ste_link: true` on every pattern that may write a bound theme into Dataverse.
- `aperture` may include `RED` only for patterns a human has named as pageable. If none are named, ship with **zero** RED patterns. Do not guess evacuation triggers.
- A pattern with `condition_id` must exist in that pack’s baseline. If `urs` has no conditions, no `urs` pattern may claim a condition id.
- Unclassified remains the default when no pattern fires.

## Bind function contract

Pure. No I/O. No Vertex. No Dataverse.

```text
bind_candidate(candidate, pack, schema, patterns, register) -> BindResult
```

`candidate` fields you may use (absent stays absent):

- title, description / body (body may be missing)
- url, source, seen_at
- GDELT: Actor1, Actor2, EventCode, ActionGeo, SOURCEURL
- proposed_triples: list from an agent, already validated, or empty
- pack id

`BindResult` (stdlib dataclass or pydantic, already in the venv):

```text
bound: bool
entity_ids: list[str]          # STE / customer / supplier / site / LOB ids
match_types: list[str]
pattern_ids: list[str]
condition_id: str | None
theme_labels: list[str]
transmission: str | None       # from existing registry_loader only
aperture: set[str]             # intersection of fired patterns
unbound_reason: str | None     # e.g. NO_REGISTER_HIT, NO_PATTERN, PACK_EMPTY
evidence_spans: list[str]      # short excerpts or GDELT fields used; no invented prose
```

Logic order:

1. Resolve pack. Empty baseline is allowed. It just cannot fire a condition.
2. Link entities from register + aliases + mapped GDELT actors, and an LOB from `lob_match`. Record match type.
3. Accept proposed triples only if `p` ∈ schema relations and every endpoint is a known class / known id. Else drop the triple. Do not repair it.
4. Derive cheap triples from GDELT when `cameo_map.yaml` has the EventCode.
5. Fire patterns against linked ids + triples + places.
6. If `require_ste_link` and `entity_ids` is empty → pattern does not fire.
7. Transmission: call existing registry bind. If it cannot bind, leave `None`. Do not default to `AMBIENT` unless the existing registry function already does that for this case. Prefer leaving it unset over guessing.
8. Return. No score. No likelihood. No `grit_risklevel`.

## Wire into existing paths — one function, two call sites

### 1. Wild scan (do this first)

Today: title word-subset against URS-TW `steThemes`, then the condition with the most overlapping labels. Hardcoded Taiwan. No GDELT store.

Change:

- Honour `--pack`. Default must be explicit in the CLI help. Do not silently use `urs-tw` if the flag says otherwise.
- Call `bind_candidate()` on each Currents row (title + description if the API gives it).
- If bound: write the existing display columns from **bind output + existing wild-scan rules**, not from a new scoring story.
  - `grit_headline` = matched condition name, else the existing unclassified sentence.
  - `grit_signalname` = article title.
  - `grit_trend` stays `NO_MATERIAL_CHANGE`.
  - `grit_risklevel` stays theme-tier mapping already in wild scan (`KEY`/`TIER_2`/`EMERGING` → HIGH/MEDIUM/LOW). Unbound = LOW.
  - `grit_businessrelevancebasis` = existing governed transmission sentence **only if** a condition fired; else say unbound / unclassified. Do not generate new Taiwan sentences.
  - `grit_evidenceconfidence` stays `LOW` for a single Currents row.
- If unbound: keep current unclassified behaviour. Still write `NEW` if today’s command already writes unbound Currents rows. Do not start dropping them.
- GDELT export rows still have no headline. Do **not** start writing them to Dataverse in this change. You may run `bind_candidate()` in memory and print entity/pattern hits for operators. Storage of GDELT remains a later spec.

Do not call `TransitionGuard` for the initial `NEW` row (spec 20 exception, already documented).

### 2. Weekly `revise()` (do this second, same function)

Today: theme keyword hit = `PER_FINDING_KEYWORD_MATCH`; else `CONDITION_DEFAULT_FALLBACK`.

Change:

- Run `bind_candidate()` on the finding text / sources already supplied. Do not scrape.
- A theme attach is `PER_FINDING_BIND` when the engine fires with a STE link.
- Keep `CONDITION_DEFAULT_FALLBACK` only if spec 32/33 still require it **and** document that it is not a STE link. Do not use fallback to claim `bound=true`.
- Do not add another `+2` for a literal theme string. If you touch `rank()`, remove the hard-coded `Taiwan business concentration` bonus or replace it with a pack-declared weight in config. Do not invent new rank terms.

Do not call `assess_impact()` from either path.

## Agents

Do not put bind inside Vertex.

If you touch `agents/` at all:

- Researcher may propose `triples` in JSON that match `schema.yaml`.
- `agents/tools.py` rejects unknown entity ids (already the rule). Extend that check to unknown relation names.
- Supervisor still rejects out-of-register citations.
- `grit_logstatus=SUCCEEDED` still means the call finished, not that bind accepted the proposal.

Default for this task: **do not change agent prompts** unless tests fail without it. Bind must work with empty `proposed_triples`.

### Query phrases (later, not this patch)

The query planner builds Boolean strings from the register, the LOB keywords, and active `ManagementRequest` scopes. An LLM may propose extra search phrases, for example a Vietnam metro tender phrase for a Vietnam request, and nothing else.

- A phrase only widens what is pulled. It is never a register term, an alias, or an STE link.
- Each phrase is logged as `PROPOSED` with its prompt version and the request or LOB that triggered it. A human may approve it into the keyword set.
- Every article pulled by a proposed phrase still goes through `bind_candidate()` unchanged. Unbound stays unbound.
- Cap the phrases per run. Source credibility and URL checks apply as before.

An event with no STE link, such as a global financial shock, is a Layer 1 question (spec 32), not a bind question. Bind keeps it unbound until the pack holds a governed Layer 1 condition for it.

## Dataverse

No new tables. No new choice codes unless an existing column already stores entity ids and is unused.

If `grit_signals` already has a field for bound entity / basis text, reuse it. If it does not, put entity ids in the existing basis or in the local `ranking.json` / printed wild-scan output only. Do not sneak a schema change into Dataverse in this patch.

Do not write severity, likelihood, publication date on a wild-scan row, or article body.

## Tests (required)

Add cases in `tests/test_bind.py`. Run `run-impact-contract-tests.py`. Do not lower the existing 163.

Minimum cases:

1. Currents-style title with a register customer alias → `bound=true`, that entity id present.
2. Same title with the alias removed → `UNBOUND`, `NO_REGISTER_HIT`.
3. “US-China rivalry” basketball headline, no register entity → unbound, no tech pattern.
4. GDELT-style dict: mapped EventCode + Actor mapped to PRC + ActionGeo Taiwan + register site in Taiwan → pattern may fire only if `patterns.yaml` and pack say so **and** site id is in the register.
5. Proposed triple with unknown `p` → dropped, no crash.
6. Proposed triple pointing at an entity id not in `known_entity_ids` → dropped.
7. `urs` pack with empty conditions → no `condition_id`, bind can still return entity ids if the workbook has entities.
8. `--pack` honour: bind for `urs` must not load URS-TW conditions.
9. Text names only an LOB keyword, no entity → `bound=true`, LOB id in `entity_ids`, match type `LOB_KEYWORD`.
10. Text matches keywords of two LOBs → unbound, `NO_REGISTER_HIT`.
11. Text names only a priority country → unbound.
12. Existing wild-scan / weekly contract tests still pass.

Use fixtures copied from real pack JSON / aliases. Do not hard-code live customer names in assertions beyond what the repo already contains. If the register in tests is a stub, say so in the test file.

## Implementation rules for Copilot CLI

1. Read `grit_scan/ontology.py`, `grit_scan/matchers/`, `dataverse/run_wild_scan.py`, `grit_scan/weekly_pipeline.py`, `grit_scan/registry_loader.py`, and the URS-TW JSON before editing.
2. Prefer extending a matcher module over a new framework.
3. Stdlib + existing deps only (`pydantic` is installed; `yaml` may not be — if PyYAML is absent, use JSON in `config/ontology/` instead of YAML. Do not `pip install` into system Python. If you must add PyYAML, add it to `requirements.txt` and the project venv only).
4. No new network calls.
5. No defaults that fill missing dates, VAR, fatality counts, or entity names.
6. Do not rename `grit_scan/ontology.py`. That name means pack loader. Call the new module `bind`.
7. Update the repository README with a short “Bind engine” subsection: what it decides, what it writes, what it does not do. Same table style as “Current shape”.
8. Stop when `bind_candidate()` is tested and wired to wild-scan Currents. Weekly `revise()` is the stretch. Red mail, GDELT Dataverse writes, harm scorer, review-to-Dataverse, and horizon packs are out of scope.

## Done

- [ ] `bind_candidate()` is pure and tested.
- [ ] Every bound hit has at least one STE / customer / supplier / site / LOB id from the pack register.
- [ ] Wild scan theme bind uses it and honours `--pack`.
- [ ] Unbound rows stay unclassified, not fallback-Taiwan.
- [ ] No LLM in the bind or score path.
- [ ] No new Dataverse columns.
- [ ] Contract runner still green.

If a step would create a fifth pipeline, stop and leave the function unwired rather than invent a parallel writer.
