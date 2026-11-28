# Identity evaluation baseline

**Plan:** `topic-collapse-plan-v2` Phase E + Phase P0  
**Code tip:** P0 branch (see latest commit on `cursor/phase-p0-identity-guards-f2b3`)  
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

### Phase P0 (this change)

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

## Labelling

- Candidate pairs: **472** (`pairs.jsonl`), of which **290** are authoritative (`label_source=ra`, `label_status=labelled`).
- Sheet: `tests/fixtures/identity_eval/labelling_sheet.csv` — `ra_label` filled from approved provisional labels.

## Reproduce

```bash
python3 tools/eval_identity.py
python3 tools/eval_identity.py --json
python3 -m pytest tests/test_identity_eval.py tests/test_topic_identity_p0.py -q
```
