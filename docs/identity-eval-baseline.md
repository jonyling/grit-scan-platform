# Identity evaluation baseline

**Plan:** `topic-collapse-plan-v2` Phase E + Phase P0 + Phase P1 + Phase P2  
**Code tip:** P2 branch (see latest commit on `cursor/phase-p2-topic-resolver-8bb9`)  
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

### Phase P1 (`7bfca9a` merge of PR #14 on `main` — LLM signatures + deterministic compare)

| Metric | Value | Notes |
|---|---|---|
| false-merge rate (authoritative) | **0.000** | At P0 (exit: at or below P0) |
| missed-merge rate (authoritative) | **0.452** | Flat vs P0 offline — see note |
| max cluster size | **6** | Unchanged |
| max distinct-country count / event | **4** | Unchanged |
| topic purity (mean, merge) | **1.000** | |
| topic completeness (mean, merge) | **0.664** | |
| authoritative pairs considered | **290** | |
| baseline_status | **authoritative** | |

**Missed-merge flat (exit-criteria note):** Offline harness uses cue-list fallback only (no EventSignature LLM, no Relation Resolver). The 56 weak SAME pairs still lack actor+action or object+action evidence under `same_event_candidate`, so they stay separate — same as P0 word-join. Production path: signature matches **nominate** when `event_relations=1` (resolver can recover), and union on the flag-off path when the closed-class signature matches. Unit fixtures: Houthi/airport duplicates join; OFAC / Taiwan defence / chip-control stay separate without term-bag luck.

### Phase P2 (this change — Topic Resolver + TopicCritic + `topic_resolutions`)

| Metric | Value | Notes |
|---|---|---|
| false-merge rate (authoritative) | **0.000** | Unchanged (topics never write SAME) |
| missed-merge rate (authoritative) | **0.452** | Unchanged |
| max cluster size | **6** | Unchanged |
| max distinct-country count / event | **4** | Unchanged |
| topic purity (mean, merge) | **1.000** | Merge-cluster proxy (P0/P1) |
| topic completeness (mean, merge) | **0.664** | Merge-cluster proxy; topics do not change merges |
| topic resolver purity (mean) | **1.000** | Gold `topics.jsonl` replay through contract+apply (no LLM) |
| topic resolver completeness (mean) | **1.000** | ≥ 0.75 exit; offline gold replay |
| persistent topic_keys (≥2 weeks) | `cyber-ot`, `middle-east-security-escalation`, `unmanned-autonomy`, `us-advanced-compute-controls` | Storyline keys persist across fixture weeks |
| 25 Nov STE topics validated | **4** | Matches PDF shape; OFAC + Saudi operating-policy stay separate |
| authoritative pairs considered | **290** | |
| baseline_status | **authoritative** | |

**P2 notes:** Flag `topic_collapse=1`. Topics never write SAME/bands/overrides/challenges. Theme candidates never change band. TopicCritic runs at most once on ambiguous borders (`0.55–0.80`). Live LLM topic purity awaits a weekly run with the flag on; offline numbers are gold-replay through the apply path.

### Phase P3 (this change — render + nightly reuse-only continuity)

| Metric | Value | Notes |
|---|---|---|
| false-merge rate (authoritative) | **0.000** | Unchanged (render/reuse never write SAME) |
| missed-merge rate (authoritative) | **0.452** | Unchanged |
| max cluster size | **6** | Unchanged |
| max distinct-country count / event | **4** | Unchanged |
| topic purity / completeness (merge) | **1.000 / 0.664** | Unchanged |
| topic resolver purity / completeness | **1.000 / 1.000** | Unchanged (gold replay) |
| authoritative pairs considered | **290** | |
| baseline_status | **authoritative** | |

**P3 notes:** `format_v2` rolls validated topics into parent rows (`Week N, status`) with indented children; unassigned elevated stay flat; emerging themes are a separate section and never change band. Nightly attaches new events to existing `topic_key`s on signature match only — no new topics overnight. Operator note: `docs/topic-collapse-operator-note.md`.

## Labelling

- Candidate pairs: **472** (`pairs.jsonl`), of which **290** are authoritative (`label_source=ra`, `label_status=labelled`).
- Sheet: `tests/fixtures/identity_eval/labelling_sheet.csv` — `ra_label` filled from approved provisional labels.

## Reproduce

```bash
python3 tools/eval_identity.py
python3 tools/eval_identity.py --json
python3 -m pytest tests/test_identity_eval.py tests/test_topic_identity_p0.py tests/test_event_signature.py tests/test_topic_collapse_p2.py tests/test_topic_collapse_p3.py -q
```
