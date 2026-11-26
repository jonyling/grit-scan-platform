# Identity evaluation fixtures (Phase E)

Built for plan v2 Phase E. **No pipeline merge behaviour is changed by these fixtures.**

## Weeks exported

- `2026-11-10-ste` — Group (STE) — local proxy for 25 Nov 2026 Strategic Radar PDF (role=group-25nov-proxy; 7 elevated events, 222 signals)
- `2026-11-02-urs-tw` — URS-Taiwan (role=urs-taiwan; 3 elevated events, 8 signals)
- `2026-11-06-marine` — Marine (role=marine; 7 elevated events, 63 signals)
- `2026-11-05-ste` — Group (STE) reasoned week (role=group; 5 elevated events, 232 signals)

## 25 Nov 2026

No `2026-11-25-*` weekly run is present under `output/runs/` or in the local pool on this machine.
The architecture PDF is dated 25 November 2026; the closest matching Group week on disk is
`2026-11-10-ste` (seven elevated rows including Houthi/Riyadh, chip/export-control, and OFAC).
It is filed under role `group-25nov-proxy`.

## Files

- `weeks/<week_id>/events.jsonl` — elevated + near-elevated events with member signals
- `weeks/<week_id>/signals.jsonl` — compact signal payloads for the eval harness
- `weeks/<week_id>/manifest.json` — provenance
- `pairs.jsonl` — candidate pairs (`signal_a`, `signal_b`, `label`); labels are empty or **provisional** only
- `labelling_sheet.csv` — sheet for Jony (fill `ra_label`: SAME | RELATED | UNRELATED)
- `topics.jsonl` — expected topic groupings (PDF skeleton + gaps)

Candidate pairs in this build: **596**.

## Labelling

Provisional labels from export co-membership / cross-event heuristics are **not** R&A-authoritative.
Only `ra_label` on the CSV (copied into `pairs.jsonl` after review) counts as ground truth.

