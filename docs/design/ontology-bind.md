# Ontology bind

This page records how GRIT-Scan decides whether a news page belongs to ST Engineering (STE), and where each rule lives in the code. It began on 2026-10-01 as a build brief for a `bind_candidate()` function. That function was built, then replaced by the link ladder, so this page now describes what the code does and what was never built.

## The question

A search pulls in pages by keyword. A keyword decides only what is fetched. It does not make a page an STE signal.

The bind answers one question about each page: does it name something on the STE register? That can be an STE entity, a customer, a supplier, a competitor, a project or site, or a line of business (LOB). If it names nothing, the page stays unlinked. The code never fills in a link to make a page count.

## Rules

These rules came from the original brief, and the code keeps them:

- **A named pack.** Every run names its reader pack, such as `urs` or `urs-tw`. No run falls back to Taiwan.
- **A register id.** A link needs a register row from the pack: an entity, customer, supplier, competitor, project, site, or LOB. A country alone is never a link, and a theme alone is never a link.
- **Country of incorporation is not a site.** A country counts only together with a LOB keyword, and only where an entity of that LOB is registered.
- **One LOB.** A LOB link needs exactly one active LOB to match. A page that matches two LOBs is not linked.
- **Recorded basis.** Each link records the matched text, where it matched (title, lead, or body), and the alias basis.
- **No score.** The bind does not score harm, estimate likelihood, or set a risk level. No language model takes part.
- **Management requests set priority only.** A management request never links a page to STE. An active request in scope makes an already-kept page priority P1.

## Where the rules live

| Rule | Code |
|---|---|
| The link ladder: `DIRECT`, `COUNTERPARTY`, `COMPETITOR`, `PERIMETER`, `SECTOR`, and `THEME` | `grit/ontology/link.py` |
| The typed graph of register rows, with a status and basis on every edge | `grit/ontology/graph.py` |
| Named projects, suppliers, customers, sites, hazards, conditions, and watchpoints in a reader pack | `grit/ontology/reader_index.py` |
| One LOB, matched by name or governed keyword | `grit/ontology/matchers/lob_matcher.py` |
| Generic public-body names, such as "Ministry of Defence", which count only with the pack's place cue | `generic_name_rule()` in `grit/pipeline/reader_scan.py` |
| News priority P1 to P4, and management-request matches | `grit/core/routing.py` |
| RED patterns, which need an event sentence, corroboration, and a named person to send | `grit/core/red_alert.py` and `config/red-patterns.json` |

A name counts only when it is strong: in the title, in the first part of the text, or named twice. A one-word name such as "Leonardo" also needs a LOB keyword or a context cue on the same page. The README describes the full rule set under "Deterministic rules".

## What changed from the brief

The brief asked for a pure `bind_candidate()` in `grit_scan/bind.py`, called first by the Dataverse wild scan and then by the weekly `revise()`. That function shipped with its tests, but no command called it. The link ladder in `grit/ontology/link.py` replaced it: the ladder grades how close a page sits to STE, where the bind returned only bound or unbound. `bind.py` was removed on 2026-10-07.

The brief's YAML files were drafts, and no code loads them:

| File | Status |
|---|---|
| `schema.yaml` | The class and relation list. The typed graph follows it, and adds `part_of`, `buys_from`, `competes_in`, and a `Theme` class. |
| `patterns.yaml` | Draft patterns that tie keywords or GDELT triples to a condition or theme. The scan plans, `config/urs-tw/URS-TW-Scan-Plan.json` and `config/urs/URS-Scan-Plan.json`, now hold the condition keywords and theme queries. |

## Not built

- **Triples from GDELT or agents.** The brief allowed GDELT event codes and agent-proposed triples (actor, relation, object) to fire patterns. Neither exists. No command calls the GDELT adapter, and agents do not propose triples.
- **A CAMEO code map.** It is not needed until GDELT is wired.
- **New Dataverse columns.** The Dataverse sync writes existing `grit_signals` columns only.

## Open questions

- Should the scan plans replace `patterns.yaml` for good, or should a pattern file return when GDELT triples are wired?
- With a strict link rule, broad stories such as US-China summit coverage stay unlinked. They reach the brief through the standing conditions and themes instead. R&A should confirm that this is the intended behavior.
