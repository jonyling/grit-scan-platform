# 47. WSH Digest and Alerts

**Status:** Built in this repository. Workplace Safety & Health for James Lek.
**Builds on:** acquisition, the signal pool, source bands, and the quote, number, citation and banned-word checks. It does not use brief format v2 (spec 44) or AIRIS packaging.

## 1. What it is

A functional track, not a business-area pack. Pack id `wsh`, level `FUNCTION`, `reportKind: "wsh-digest"`. It monitors external Workplace Safety & Health signals for industries STE operates in, and writes a monthly discussion digest plus interim alerts. The reader is James Lek (`config/profiles/james.json`). There is no `BA_HEAD` review role, so nothing from this pack is packaged for AIRIS.

`FUNCTION` may have no parent and an empty commercial graph. `graph_pack` returns the pack itself. `reportKind` selects the digest renderer and skips `format_v2.build`.

The command is `grit digest --pack wsh --date YYYY-MM-DD`. It is not on the Thursday schedule in `grit/schedule.py`. Business packs and `config/red-patterns.json` stay unchanged for those packs.

## 2. Digest sections

Five sections. There is no section 2. A regulatory change does not get its own essay: it raises an alert, and the next digest places a short note under the topic it changes.

| Id | Section | Content |
|---|---|---|
| `SERIOUS` | Serious incidents | Fatal and serious injuries from falls from height, struck-by, caught-in, electrical, lifting, confined space, and fire. A few incidents are discussed in full, including investigation findings and lessons learnt when published. The rest go to a log: title, date, place, energy, source. |
| `LEADING` | Leading indicators | Good practices from the Campbell Institute, ICMM, and named industry leaders: what the practice is, the evidence they cite, and where it could apply. |
| `SIF` | Energy Wheel and SIF prevention | Work by Dr Matthew Hallowell and the Construction Safety Research Alliance (Energy Wheel, SCL model, HECA and direct controls), plus SIF prevention practices and precursors from EEI, the National Safety Council, and industry leaders. Apply those lenses to a serious-incident item only when the source gives enough to do so. |
| `HFACS` | Human factors — HFACS | Human Factors Analysis and Classification System (Dr Douglas Wiegmann and Dr Scott Shappell), including industry applications. |
| `EMERGING` | Emerging risks | Heat stress, fatigue, ageing workforce, contractor management, and new technologies. |

Standing descriptions of the Energy Wheel, the SCL model, HECA, and HFACS are a short fixed box plus what changed this month. The model is not re-taught every month.

## 3. Evidence rule and reasoning

Each kept item is a short argued note: what the source establishes, the date and place, what is not established, the published lesson or practice, and a possible implication for one of the six industries. Possibility language stays (`could`, `may`, `might`). A lesson, an Energy Wheel classification, an SCL or HECA label, or an HFACS tier that the source does not support is withheld and marked not established. Figures must appear in a cited source. The same quote, number, citation and banned-word checks used by the Assessor apply.

Digest writing borrows the Assessment → Critic pattern from the main reasoned weekly chain (spec 45), adapted for WSH:

1. A **wide deterministic prefilter** (`candidate_for_reasoning`) nominates method papers, Singapore workplace/hazard/emerging items, industry workplace items, and framework sightings. Hard excludes (residential, clinical, combat, passenger-lifestyle) still drop before the model.
2. **Assessment** states fact → STE exposure → transmission → impact/lesson for every nominee and proposes INCLUDE, WATCH, EXCLUDE or DEFER.
3. The **Critic** challenges the weakest link (false positives, false negatives, unsupported attribution) and returns a full selections list.

Theme sightings alone never keep an item. INCLUDE/WATCH notes surface STE exposure and transmission in the Markdown and Word digest.

### 3.1 Council bulletins, clusters, and precedents

Standing theme `WSH-BULLETIN-01` searches the Workplace Safety and Health Council Alert, Advisory, and Insights series (`site:wshc.sg`). It is not a sixth digest section. A **Council line** under the bottom line names every series item in the window, including empty series ("No WSH Alert in this window"). Image-only alert PDFs are not OCR'd; the HTML view-in-browser page is preferred.

When the lead source is a Council bulletin, the note carries what happened, earlier cases the bulletin names, the numbered "what companies should do" list, the named instrument, investigation status (preliminary when the bulletin says so), and a penalty figure only if stated. That control list is `lesson_or_practice`. After the Critic, notes that share a normalised subject (heat stress, haze / PM2.5, safety time-out, SIF alliance, or the same Council title) collapse to one note, preferring a Council or MOM page. A safety time-out or after-N-deaths enforcement campaign routes to Serious incidents with the matched high-energy hazard as energy.

Earlier cases are stored in `wsh_precedents` only when a bulletin names a date. The next digest may surface them on a note with the same hazard as "A prior WSH Council bulletin named…". The model may not invent a match.

## 4. Scope and geography

Scope lives in `config/wsh/WSH-Scope.json`: six industries (defence, aerospace, marine, construction, facilities management, security services), high-energy hazards, framework bodies, and the geography phase.

- **Phase 1 — Singapore.** Incident and regulatory items must be Singapore. Method papers (Campbell, ICMM, CSRA, Hallowell, EEI, NSC) may be from anywhere; the implication is written for those industries in Singapore. A generic accident with no industry and no Singapore place is dropped.
- **Phase 2 — DPS overseas.** Add locations only from a list James confirms. The countries on the Digital Systems & Cyber exposure register are not that list.

## 5. Alerts

Two kinds. A hit drafts an alert for a named person to send or dismiss. Nothing is sent by code. A hit does not set business-report RED status.

1. **Serious incident.** A fatality or serious injury from a high-energy hazard in a watched place and industry.
2. **Regulatory change.** From MOM, the Workplace Safety and Health Council, the Workplace Safety and Health Act, or its subsidiary legislation.

A method paper does not alert. Alerts are a `wsh`-only daily read: MOM and WSH Council where a feed or a single Currents query works, plus pack-local patterns in `config/wsh/WSH-Alert-Patterns.json`. Feeds declare `"packs": ["wsh"]` so the existing daily lane for other packs is unchanged.

## 6. Cadence and sources

Monthly digest. Phase-1 sources: Ministry of Manpower, Workplace Safety and Health Council, Singapore Statutes Online (WSHA), SCDF for fire; practice sources Campbell Institute, ICMM, CSRA, EEI, National Safety Council. Queries in `WSH-Scan-Plan.json` are anchored (`Singapore construction fall from height fatality`, `WSH Council heat stress circular`, `site:wshc.sg "WSH Alert"`), not bare words like `workplace safety`. The standing theme set casts a wide net across the six industries, high-energy hazards, MOM/WSHC circulars, Council bulletin series, and named method bodies (Campbell, ICMM, CSRA/Hallowell, EEI, NSC SIF, HFACS).
