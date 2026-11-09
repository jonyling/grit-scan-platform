# Workplace Safety & Health

The `wsh` pack monitors external Workplace Safety & Health signals for industries ST Engineering operates in. It writes a monthly discussion digest and interim serious-incident and regulatory alerts for James Lek. It is not a business-area pack. Governing spec: [47 — WSH Digest and Alerts](../../specs/47_WSH_Digest_and_Alerts.md).

Business-area weekly briefs stay on `grit weekly`. This pack is not on the Thursday schedule in `grit/schedule.py`.

## The rule

Agents propose. Deterministic functions decide. A named human reviews.

- No scoring path calls a language model for severity or RED.
- A missing fact stays **not established**. Lessons, Energy Wheel / SCL / HECA / HFACS labels, and penalty figures appear only when a source states them.
- Nothing is published by code. Digest packages and alert drafts wait for a person.

## What it produces

| Output | Cadence | Content |
|---|---|---|
| Monthly digest (Word + Markdown) | On demand via `grit digest` | Five sections: Serious incidents; Leading indicators; Energy Wheel and SIF prevention; HFACS; Emerging risks. No separate section for regulation. |
| Council line | With each digest | Every WSH Alert / Advisory / Insights in the window, including empty series. |
| Serious-incident and regulatory alerts | Daily lane, this pack only | Drafts only; not sent by code. Method papers never alert. |

Geography phase 1: Singapore is the workplace or regulatory locus for incidents and circulars. Method papers (Campbell, ICMM, CSRA/Hallowell, EEI, NSC, HFACS) may be global. Phase 2 adds DPS places only when James confirms a list.

## Layout

```
config/wsh/           Pack ontology: scope, themes, scan plan, sources, alert patterns
config/profiles/      james.json — WSH digest reader
grit/wsh/             Functional track code (scope, reason, digest, bulletins, alerts)
examples/             Reference WSH Council Alert / Advisory / Insights PDFs
specs/47_*.md         This pack's governing spec
tests/test_wsh_*.py   Fixture and contract tests
output/runs/…-wsh/    Digest and alert drafts (gitignored)
```

### `grit/wsh/`

| Module | Role |
|---|---|
| `scope.py` | Classify place, industry, hazard, framework; hard STE gate and soft reasoning prefilter |
| `bulletins.py` | Detect Alert / Advisory / Insights; Council line; cluster collapse; enforcement routing |
| `reason.py` | Assessment → Critic loop (spec 45 pattern for WSH) |
| `digest.py` | Collect notes, render Markdown/Word, write package |
| `precedents.py` | Store earlier cases only when a Council bulletin names a date |
| `alerts.py` | Pack-local serious / regulatory alert drafts |
| `prompt.py` / `relevance.py` | Prompt versions and optional single-item relevance |

## Run

```bash
.venv/bin/python -m grit digest --pack wsh --date YYYY-MM-DD --profile james --no-submit
```

- `--dry-run` prints the query plan and spends nothing.
- Digest writing defaults to Assessment → Critic. Fixture tests use the deterministic path (`use_reasoned=False`).
- Re-report without a new Brave spend: `.venv/bin/python -m grit report --pack wsh --date YYYY-MM-DD --profile james --no-submit`.

Tests:

```bash
.venv/bin/python -m pytest tests/test_wsh_digest.py -q
```

## Reference bulletins

[`examples/`](../../examples/) holds three Workplace Safety and Health Council products used as the depth target for operational notes:

- `20241213_wsh_alert.pdf` — fatal fall through a skylight (scanned images; prefer HTML “view in browser” when scanning)
- `20261007_wsh_advisory.pdf` — concrete pumping fatalities and numbered controls
- `20261008_WSH_Insights.pdf` — small-scale construction works and an inspection push

Standing queries under theme `WSH-BULLETIN-01` target those series on `wshc.sg`.
