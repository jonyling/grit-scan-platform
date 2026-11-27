# Identity evaluation fixtures (Phase E)

Built for plan v2 Phase E. **No pipeline merge behaviour is changed by these fixtures.**

## Weeks exported (Wilson DOCX — authoritative)

- `2026-11-25-ste` — Group (STE) — 25 Nov 2026 Strategic Radar (Wilson DOCX) (role=group; 7 elevated events, 59 signals)
- `2026-11-26-urs` — URS — 26 Nov 2026 (Wilson DOCX) (role=urs; 12 elevated events, 91 signals)
- `2026-11-19-urs` — URS — 19 Nov 2026 (Wilson DOCX) (role=urs; 12 elevated events, 48 signals)
- `2026-11-25-marine` — Marine — 25 Nov 2026 (Wilson DOCX) (role=marine; 11 elevated events, 68 signals)
- `2026-11-19-marine` — Marine — 19 Nov 2026 (Wilson DOCX) (role=marine; 9 elevated events, 77 signals)
- `2026-11-24-ca` — Commercial Aerospace — 24 Nov 2026 (Wilson DOCX) (role=ca; 8 elevated events, 61 signals)

## Provenance

- Wilson DOCX directory used: `/cursor/stores/bc-ad02b207-afda-408c-997d-5188616d063c/internal/topic-collapse/wilson-briefs`
- **Pool / weekly-run gap:** `output/grit.sqlite` and matching `output/runs/<week_id>` were not available on the builder host. Fixtures are built from Wilson DOCX elevated item tables + Appendix B source registers. Signal IDs are stable hashes of URL/title (not pool `SIG-*` ids). Re-run this builder on a host with the pool to enrich text/lanes.
- URS-Taiwan pack week is **not** in the attached six briefs; URS Group weeks `2026-11-19-urs` and `2026-11-26-urs` stand in for the URS role.

## Files

- `weeks/<week_id>/events.jsonl` — elevated + near-elevated events with member signals
- `weeks/<week_id>/signals.jsonl` — compact signal payloads for the eval harness
- `weeks/<week_id>/manifest.json` — provenance
- `pairs.jsonl` — candidate pairs (`signal_a`, `signal_b`, `label`); labels are empty or **provisional** only
- `labelling_sheet.csv` — sheet for Jony (fill `ra_label`: SAME | RELATED | UNRELATED)
- `topics.jsonl` — expected topic groupings seeded from Wilson elevated items

Candidate pairs in this build: **472**.

## Labelling

Provisional labels from export co-membership / cross-event heuristics are **not** R&A-authoritative.
Only `ra_label` on the CSV (copied into `pairs.jsonl` after review) counts as ground truth.

