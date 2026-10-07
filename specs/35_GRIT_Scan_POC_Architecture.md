# 35. GRIT-Scan POC Architecture

**Status:** Draft for review. Describes the code as built on 2026-10-07.
**Supersedes:** Nothing. Specs 13–34 still govern the rules. This spec records how the proof of concept meets them, and it is the baseline for the MVP specifications written under Specifications-Driven Development (SDD).

## 1. Purpose

GRIT-Scan scans the external environment for risks that could affect ST Engineering's business performance, and briefs each executive in the form they need. The proof of concept serves the Group Head of Risk & Assurance (Wilson) and the Urban Solutions (URS) business partner (Hwaimay). It runs two reader packs: Urban Solutions, the first business-area (BA) pack, and URS Taiwan, an entity pack under it. The other six business areas follow the Urban Solutions pattern ([Add a business area](../docs/add-a-business-area.md)).

The method follows the M365 Copilot ontology pattern, inverted. Copilot asks, then retrieves from a graph. GRIT-Scan pulls the web with keywords, then binds what it pulled to the STE register. Keywords decide what is fetched. The register decides what belongs to STE.

## 2. Invariants

Spec 34 section 1 lists ten invariants. The POC keeps all of them. Three matter most for the design:

- Agents propose. Deterministic functions decide. A named human approves.
- No scoring path calls a language model. Confidence, priority, trend labels, RED status, and the harm verdict are rules.
- A missing fact stays missing. The brief prints "not established" or "nothing found", never a default.

## 3. Structure

A deterministic layer sits under six agent roles.

| Role | What it proposes | What decides | Code |
|---|---|---|---|
| Supervisor | The order of steps for a reader and week. | `TransitionGuard()` on every workflow advance; `ReviewGate()` on every package. | `grit/pipeline/reader_scan.py`, `grit/workflow/` |
| Researcher | Search results for each query, and one follow-up search per thin finding. | The query plan, the monthly search guard, URL canonicalisation, and the dedupe rule. | `grit/ontology/reader_plan.py`, `grit/acquisition/` |
| Assessor | A draft for each standing condition: movement and a cited summary. The finding card's answers. | Quote, number, citation, and banned-word checks. The confidence rule. | `grit/agents/reader.py`, `grit/agents/loop/` |
| Critic | Issues with a draft: stale, unsupported, or contradictory claims. | An issue counts only with a verbatim quote from a supplied source. | `grit/agents/reader.py` |
| Profiler | The reader's preferences, from their approve and reject decisions. | A bounded memory rule that reorders within a tier and can hide what the reader rejected, never a P1 or RED signal. | `grit/profiles/memory.py` |
| Reporter | Entries for named items, the bottom line, and chat answers. | Checks, then one brief model rendered to every format. | `grit/agents/reader.py`, `grit/agents/chat.py`, `grit/report/` |

## 4. Data flow

1. **Ontology.** Each reader pack is a folder of JSON registers under `config/` with a `pack.json` manifest: its level (GROUP, BA, or ENTITY), parent, children, register files, and, for a BA, the inputs its builder needs. `grit pack-build` builds a BA pack from its Entity Info workbook. The `urs-tw` entity pack adds ten standing conditions, named projects, suppliers, customers, sites, watchpoints, and order-book anchors. The risk cascade orders entity, BA, and Group risk registers by weight.
2. **Plan.** Each pack's scan plan becomes the week's queries. An entity pack queries its standing conditions and named items. A BA pack queries its 14 risk themes with sector-anchored phrases, its active management requests, and its entities, customers, suppliers, and competitors from the typed graph, rotating the long lists by cadence. Each query names its target register row.
3. **Acquire.** Brave News returns pages. Currents can add two pulls. Each page becomes one signal in the pool, keyed by its canonical URL, with a sighting per query.
4. **Embed.** Gemini embeddings go into the pool, through a text-hash cache.
5. **Assess.** Deterministic functions run on every signal: document type, the business-area link ladder, reader-pack hits, management-request matches, the keep rule, briefing priority, the risk cascade, the harm scorer, relevance labels, and the RED rule. Results are stored per signal, run, and pack.
6. **Agents.** The Assessor drafts each standing condition or theme. A theme an entity pack covers draws on that pack's signals for the same date. The Critic reviews all drafts. Accepted issues go back to the Assessor once and are logged as QA corrections. The Reporter writes item entries and the bottom line; a BA bottom line also reads its entity packs' bottom lines. Watchpoints update by rule. The finding loop writes six-question cards, taking management-request matches first, then theme-bound signals.
7. **Route.** For each reader, `ApertureRouting()` assigns P1 to P4 and includes or suppresses each signal. Memory reorders within a tier.
8. **Report.** One brief model per reader renders to Word, PowerPoint, Excel, and Markdown. RED patterns write draft emails. Nothing is sent.
9. **Review.** Each reader's package runs `NEW` to `PENDING_REVIEW` through the guard. A reviewer's decision goes through `ReviewGate()`. Signals the BA head approved, inside an approved package, move to `AIRIS_HANDOFF_READY` and are exported.
10. **Sync.** Kept signals push to Dataverse `grit_signals` with existing columns and choice codes.

## 5. Signal pool

The pool is one SQLite file (`output/grit.sqlite`, or `GRIT_DB`). Its schema is `grit/pool/schema.sql`. Shared facts and reader-scoped judgements are separate tables, as spec 22 requires.

| Table | Holds | Spec |
|---|---|---|
| `signals`, `sightings`, `vectors` | Pages, which query found them, and embeddings. | 27 (`GRIT_Signals`) |
| `assessments` | Every deterministic result per signal, run, and pack. | 15, 29, 30 |
| `condition_assessments`, `exposure_checks`, `watchpoints`, `qa_log`, `findings` | The weekly cascade and its corrections. | 32, 33 |
| `feedback`, `management_requests` | Reader decisions and requests. | 22 (`GRIT_TrackingFeedback`), 29 |
| `workflow_states` | The append-only ledger. Triggers refuse `UPDATE` and `DELETE`. | 18 |
| `packages`, `alerts`, `airis_exports` | Reader packs, RED drafts, and AIRIS handoffs. | 19, 25 |
| `execution_log` | Every agent and logged function call, with versions and hashes. | 17, 27 |

The pool is the working store. Dataverse remains the system of record. The MVP decides whether the pool stays as a cache or Dataverse becomes the only store.

## 6. Interfaces

| Interface | POC | MVP direction |
|---|---|---|
| RED | Draft `.eml` per RED signal, addressed by profile. A named person sends it. | Mail through a governed sender after a named approval. |
| Weekly brief | Word, PowerPoint, Excel, and Markdown per reader. | Same formats, published after `ReviewGate()`. |
| Dashboard | Streamlit: conditions, named items, watchpoints, signals with detail, packs and review, requests, RED, and AIRIS. | Next.js (React), Tailwind, and shadcn/ui over an API. |
| Chat | Questions answered from the reader's shown signals with checked citations. `/concern`, `/topic`, `/trend`, and `/keyword` propose management requests. | Same contract behind the API. |

## 7. Acceptance as built

These hold on 2026-10-07 and are covered by `tests/`:

- An offline run goes from search to an approved package and an AIRIS handoff, with a guard row on every advance.
- A reader-only profile cannot review. An ineligible attempt is refused before `ReviewGate()`.
- Two readers get different views over identical shared signals (wide and material apertures).
- A P1 needs an active request. A proposed request assigns nothing.
- A RED pattern in a forecast sentence is not RED. One unrated outlet is not enough.
- A draft with an unquoted claim, a number not in its source, or a score word is withheld.
- A BA run binds names through the typed graph, assesses 14 themes, lists every entity, customer, and supplier, and carries its entity pack's bottom line. The entity head receives no BA brief.
- Each package version can be returned for rework and replaced; a guard refusal is reported, not hidden.

The live run `2026-10-07-urs-tw` searched 60 queries, kept 240 of 793 pages, validated all ten conditions, and produced a brief whose structure matches the 2 Oct 2026 reference. The live run `2026-10-07-urs` searched 118 queries, kept 324 of 1,096 pages, assessed all 14 themes (10 with evidence, 4 with nothing found), and listed P1 matches for eight of nine active management requests.

## 8. Open for the MVP

Write each item as trigger, deterministic decision, who signs off, and what is logged (spec 34 section 7).

1. Sphera supplier alerts and GDELT are not wired into the reader scan. Sphera has credentials and no documented API contract here.
2. The harm scorer's severity stays not established: acquisition supplies prose, not structured observations, and no value-at-risk register exists.
3. Source ratings cover 26 Taiwan outlets. Most business-area sources are unrated.
4. Order-book anchors are copied from the reference brief and need a named BA owner's confirmation.
5. The BA relationship gaps (spec 34 section 5 and the README) still limit entity-level reasoning.
6. Scheduling, mail delivery, authentication, and the Dataverse ledger write path are not built.
7. The six other business areas have no packs yet. Adding one is a data change: a manifest, the workbook build, a scan plan, and profile briefs. `grit gaps`, the digest commands, and the harm scorer's transmission registry still read the URS pack or workbook.
8. No URS-wide standing conditions exist. The BA brief stands on the 14 risk themes until R&A approves a condition set.
