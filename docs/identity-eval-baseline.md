# Identity evaluation baseline (Phase E)

**Plan:** `topic-collapse-plan-v2` Phase E  
**Code tip:** `67f5e61` (`origin/main` — Ground staff exposure for all tracker areas)  
**Recorded:** 2026-11-27 (Asia/Singapore)  
**Harness:** `tools/eval_identity.py` against `tests/fixtures/identity_eval/`  
**Pipeline behaviour:** unchanged (fixtures + harness + docs/tests only)

## Weeks exported (Wilson DOCX — authoritative)

| week_id | Role | Notes |
|---|---|---|
| `2026-11-25-ste` | Group | Wilson DOCX `GRIT-Scan_ste_wilson_2026-11-25_9883.docx` — 7 elevated (Houthi/Iran alerts, chip/BIS/Megaspeed, OFAC, Saudisation) |
| `2026-11-26-urs` | URS | Wilson DOCX `GRIT-Scan_urs_wilson_2026-11-26_5ebe.docx` |
| `2026-11-19-urs` | URS | Wilson DOCX `GRIT-Scan_urs_wilson_2026-11-19_bb8c.docx` |
| `2026-11-25-marine` | Marine | Wilson DOCX `GRIT-Scan_marine_wilson_2026-11-25_0cfe.docx` |
| `2026-11-19-marine` | Marine | Wilson DOCX `GRIT-Scan_marine_wilson_2026-11-19_ba02.docx` |
| `2026-11-24-ca` | CA | Wilson DOCX `GRIT-Scan_ca_wilson_2026-11-24_8a97.docx` (Plus Ultra among elevated) |

**URS-Taiwan:** not in the attached six briefs; URS Group weeks stand in for the URS role.

**Pool / weekly-run gap:** builder host had no `output/grit.sqlite` or matching `output/runs/<week_id>`. Fixtures are built from Wilson DOCX elevated-item tables + Appendix B source registers. Signal IDs are stable URL/title hashes (not pool `SIG-*`). Re-run `tools/build_identity_eval_fixtures.py` on a host with the pool to enrich text/lanes.

## Headline numbers (`67f5e61`)

| Metric | Value | Status |
|---|---|---|
| false-merge rate (authoritative) | n/a | **Blocked on Jony R&A labels** (`ra_label` in labelling sheet) |
| missed-merge rate (authoritative) | n/a | **Blocked on Jony R&A labels** |
| false-merge rate (provisional export heuristics) | **0.000** | Indicative only — not ground truth |
| missed-merge rate (provisional export heuristics) | **0.145** | Indicative only — brief co-members that do not share a distinctive headline term stay apart under offline `_group` |
| max cluster size | **6** | Observed on offline `_group`+`_merge_stories` over elevated+near signals |
| max distinct-country count / event | **4** | Via `grit.core.countries.named` on member titles/text |
| topic purity (mean) | **1.000** | Wilson-seeded topic buckets; provisional until R&A confirms |
| topic completeness (mean) | **0.767** | Same caveat |

`baseline_status`: **provisional_only**

## Labelling

- Candidate pairs: **472** (`pairs.jsonl`), of which **450** carry hard-case flags.
- Sheet for R&A: `tests/fixtures/identity_eval/labelling_sheet.csv` — fill `ra_label` with `SAME` \| `RELATED` \| `UNRELATED`. Absolute path on this worker:  
  `/agent/repos/grit-scan-platform/tests/fixtures/identity_eval/labelling_sheet.csv`
- Provisional labels in `pairs.jsonl` use `label_status=provisional`, `authoritative=false`, `label_source=export_heuristic`. **Do not treat them as R&A truth.**

## Reproduce

```bash
.venv/bin/python tools/eval_identity.py
.venv/bin/python tools/eval_identity.py --json
.venv/bin/pytest tests/test_identity_eval.py -q
```

To regenerate fixtures from Wilson DOCX (+ optional local pool):

```bash
WILSON_DOCX_DIR=/path/to/wilson-briefs .venv/bin/python tools/build_identity_eval_fixtures.py
```

## After R&A labels

1. Copy `ra_label` values into `pairs.jsonl` with `authoritative: true`, `label_status: labelled`, `label_source: ra`.
2. Re-run `tools/eval_identity.py` and replace the authoritative rows in this document.
3. Every later phase PR quotes these headline numbers vs the new run.
