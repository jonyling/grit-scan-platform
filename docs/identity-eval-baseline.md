# Identity evaluation baseline (Phase E)

**Plan:** `topic-collapse-plan-v2` Phase E  
**Code tip:** `acb7727` (`origin/main` — Harden semantic weekly report recovery)  
**Recorded:** 2026-11-26 (Asia/Singapore)  
**Harness:** `tools/eval_identity.py` against `tests/fixtures/identity_eval/`  
**Pipeline behaviour:** unchanged (fixtures + harness + docs/tests only)

## Weeks exported

| week_id | Role | Notes |
|---|---|---|
| `2026-11-10-ste` | Group — **25 Nov proxy** | No local `2026-11-25-*` run. PDF dated 25 Nov 2026; this week has seven elevated rows (Houthi/Riyadh, chip/export-control, OFAC) matching the PDF narrative. |
| `2026-11-02-urs-tw` | URS-Taiwan | Local weekly run + Wilson brief |
| `2026-11-06-marine` | Marine | Restored run; selections recovered from Wilson brief |
| `2026-11-05-ste` | Group | Full reasoned pool assessments |

## Headline numbers (`acb7727`)

| Metric | Value | Status |
|---|---|---|
| false-merge rate (authoritative) | n/a | **Blocked on Jony R&A labels** (`ra_label` in labelling sheet) |
| missed-merge rate (authoritative) | n/a | **Blocked on Jony R&A labels** |
| false-merge rate (provisional export heuristics) | **0.000** | Indicative only — not ground truth |
| missed-merge rate (provisional export heuristics) | **0.887** | Indicative only — brief co-members often not re-joined by offline `_group` without full citation/SAME ledger |
| max cluster size | **31** | Observed on offline `_group`+`_merge_stories` over elevated+near signals |
| max distinct-country count / event | **12** | Via `grit.core.countries.named` on member titles/text |
| topic purity (mean) | **0.562** | Only `2026-11-10-ste` has PDF substring pre-fill; provisional |
| topic completeness (mean) | **0.474** | Same caveat |

`baseline_status`: **provisional_only**

## Labelling

- Candidate pairs: **596** (`pairs.jsonl`), of which **450** carry hard-case flags.
- Sheet for R&A: `tests/fixtures/identity_eval/labelling_sheet.csv` — fill `ra_label` with `SAME` \| `RELATED` \| `UNRELATED`.
- Provisional labels in `pairs.jsonl` use `label_status=provisional`, `authoritative=false`, `label_source=export_heuristic`. **Do not treat them as R&A truth.**

## Reproduce

```bash
.venv/bin/python tools/eval_identity.py
.venv/bin/python tools/eval_identity.py --json
.venv/bin/pytest tests/test_identity_eval.py -q
```

To regenerate fixtures from local `output/runs` + `output/grit.sqlite` (optional):

```bash
.venv/bin/python tools/build_identity_eval_fixtures.py
```

## After R&A labels

1. Copy `ra_label` values into `pairs.jsonl` with `authoritative: true`, `label_status: labelled`, `label_source: ra`.
2. Re-run `tools/eval_identity.py` and replace the authoritative rows in this document.
3. Every later phase PR quotes these headline numbers vs the new run.
