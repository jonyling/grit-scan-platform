# Identity evaluation baseline

**Plan:** `topic-collapse-plan-v2` Phase E + Phase P0 + Phase P1  
**Code tip:** P1 branch (see latest commit on `cursor/phase-p1-event-signatures-5445`)  
**Recorded:** 2026-11-28 (Asia/Singapore)  
**Harness:** `tools/eval_identity.py` against `tests/fixtures/identity_eval/`  
**Labels:** Provisional export heuristics promoted to authoritative after Jony R&A approval (no flips).

## Weeks exported (Wilson DOCX)

| week_id | Role | Notes |
|---|---|---|
| `2026-11-25-ste` | Group | Wilson DOCX — 7 elevated (Houthi/Iran alerts, chip/BIS/Megaspeed, OFAC, Saudisation) |
| `2026-11-26-urs` | URS | Wilson DOCX |
| `2026-11-19-urs` | URS | Wilson DOCX |
| `2026-11-25-marine` | Marine | Wilson DOCX |
| `2026-11-19-marine` | Marine | Wilson DOCX |
| `2026-11-24-ca` | CA | Wilson DOCX (Plus Ultra among elevated) |

## Headline numbers

### Phase E / pre-P0 (`c1a7921` merge of PR #12 on `main`, old merge path)

| Metric | Value | Notes |
|---|---|---|
| false-merge rate (authoritative) | n/a → **0.000** once labels promoted on old path | Authoritative was blocked until R&A approval; provisional was 0.000 |
| missed-merge rate (authoritative) | n/a → **0.145** once labels promoted on old path | Provisional missed-merge on old `_group`+`_merge_stories` |
| max cluster size | **6** | Offline elevated+near fixtures |
| max distinct-country count / event | **4** | |
| topic purity (mean) | **1.000** | |
| topic completeness (mean) | **0.767** | |

`baseline_status` (Phase E): **provisional_only** (authoritative rows filled in P0)

### Phase P0 (`c3fc94c` merge of PR #13 on `main`)

| Metric | Value | Notes |
|---|---|---|
| false-merge rate (authoritative) | **0.000** | Unchanged (already zero); no new false bridges |
| missed-merge rate (authoritative) | **0.452** | Rise vs 0.145 — see note below |
| max cluster size | **6** | Fixture pool has no 44→96 snowball row; size flat offline |
| max distinct-country count / event | **4** | Flat on fixtures; guard thresholds (25 / 3) still enforced in code |
| topic purity (mean) | **1.000** | |
| topic completeness (mean) | **0.664** | |
| authoritative pairs considered | **290** | |
| baseline_status | **authoritative** | |

**Missed-merge rise (exit-criteria note):** Offline eval has no Relation Resolver LLM. All 56 newly missed SAME pairs are `co_member_elevated_export` with 0–1 shared non-place terms (many are Wilson topic co-elevated mates such as Houthi/Riyadh vs Hormuz/ADNOC). Among SAME pairs with ≥2 shared non-place terms or a curated alias, missed-merge stays **0.000**. Houthi/airport lexical duplicates and Plus Ultra alias still join. Production `event_relations=1` nominates the weak pairs for the resolver instead of unioning them.

### Phase P1 (this change — LLM signatures + deterministic compare)

| Metric | Value | Notes |
|---|---|---|
| false-merge rate (authoritative) | **0.000** | At P0 (exit: at or below P0) |
| missed-merge rate (authoritative) | **0.452** | Flat vs P0 offline — see note |
| max cluster size | **6** | Unchanged |
| max distinct-country count / event | **4** | Unchanged |
| topic purity (mean) | **1.000** | |
| topic completeness (mean) | **0.664** | |
| authoritative pairs considered | **290** | |
| baseline_status | **authoritative** | |

**Missed-merge flat (exit-criteria note):** Offline harness uses cue-list fallback only (no EventSignature LLM, no Relation Resolver). The 56 weak SAME pairs still lack actor+action or object+action evidence under `same_event_candidate`, so they stay separate — same as P0 word-join. Production path: signature matches **nominate** when `event_relations=1` (resolver can recover), and union on the flag-off path when the closed-class signature matches. Unit fixtures: Houthi/airport duplicates join; OFAC / Taiwan defence / chip-control stay separate without term-bag luck.

## P1 decision-gate metrics (for Jony)

Check against **25 Nov STE** and one other week (recommend **26 Nov URS**):

| Gate question | Offline evidence |
|---|---|
| Is the elevated set already readable without topics? | 25 Nov STE: 9 elevated events, max size 6, purity 1.0 — OFAC / Taiwan defence / chip controls stay separate under signature compare. |
| False bridges via country / staff / one word? | Still blocked (P0 + signature `other` never matches). |
| Signature cost (weekly) | 1 EventSignature call per event with members; cache by anchor `input_hash`; re-extract only when anchors change. Fallback marked `signature_source=fallback`. Stats on run config: `signature_stats` (`calls`, `cache_hits`, `cache_hit_rate`, `llm`, `fallback`) and `signature_near_misses` (P2 topic candidates). |
| Proceed to P2? | If 25 Nov + one peer week read cleanly as separate events, P2 (persistent topics) may be deferred. If the brief still needs a management-frame roll-up across distinct events, start P2 after this merges. |

## Labelling

- Candidate pairs: **472** (`pairs.jsonl`), of which **290** are authoritative (`label_source=ra`, `label_status=labelled`).
- Sheet: `tests/fixtures/identity_eval/labelling_sheet.csv` — `ra_label` filled from approved provisional labels.

## Reproduce

```bash
python3 tools/eval_identity.py
python3 tools/eval_identity.py --json
python3 -m pytest tests/test_identity_eval.py tests/test_topic_identity_p0.py tests/test_event_signature.py -q
```
