# Reasoned weekly: progress and handover, 2–4 November 2026

This page summarizes the work of 2–4 November 2026 on the reasoned weekly
pipeline (`grit weekly --reasoned`). It covers what was built, how it was
verified, what is still weak, and what to do next. It is written for the next
developer or agent, who works in a separate worktree.

## Goal

GRIT-Scan's weekly business-area reports should match two references:

- The URS methodology,
  [M365_Copilot_GRIT-Scan_URS_Methodology.docx](../M365_Copilot_GRIT-Scan_URS_Methodology.docx).
- Wilson's own report,
  [GRIT_Scan_URS_BA-wide_22-29Oct2026_v2.docx](../GRIT_Scan_URS_BA-wide_22-29Oct2026_v2.docx).

The original design is in
[GRIT-Scan_Wilson_Discussion_2026-08-24.pptx](../GRIT-Scan_Wilson_Discussion_2026-08-24.pptx).
The deterministic layer finds and screens pages. The agents argue over the
pool: Assessment, Critic, Researcher, Reporting, Memory, and a Supervisor.
Scores, status and RED stay rule-owned or human-owned.

The test week is URS, 2 November 2026 (run `2026-11-02-urs`).

## Results

| | Validation 10 | Validation 13 | Validation 15 |
|---|---|---|---|
| Report readiness | READY | READY | READY, one 20-minute pass |
| Scorecard checks passed | 3 of 6 | 6 of 6 | 7 of 7 |
| Status | WATCHPOINT | ELEVATED | ELEVATED |
| Escalating rows | 1 (score 7) | 8 (scores 9–10) | 7 (scores 8–10) |
| Share of cited sources rated T1–T3 | under 0.1 | 0.08 | 0.18 |
| Key risks with response options | 2 of 6 | 5 of 6 | 5 of 6 |

The status was ELEVATED in every run of this week since validation 11. The
Saudi item is led by the US Embassy security alert (T1). The R&A operator
judges the report to be about 20–30% short of Wilson's M365 Copilot report.

## Commits

| Commit | Summary |
|---|---|
| `8c5fd80` | Reasoned weekly agent review and reporting |
| `fd20a36` | Safely resumable reasoned reports (`grit resume-report`) |
| `15f116c` | URS methodology alignment: search ledger, freshness, internal exposure records, 0–14 significance score, status policy, contradiction search, Critic logic chain, RED confirmation by a named R&A operator, `resume-report --refresh` |
| `e07db34` | Source bands, rule-based evidence, significance-led escalating rows, same-event sources, exposure decides relevance, official search lanes, ten RED triggers, exposure snapshot input, `grit scorecard` |
| `ef53c24` | Methodology report order, action posture section, corrections ledger (`grit corrections`), dateline-free look-ahead |
| `b50c4df` | Report stage keeps running on a minor Critic error; refresh recovers a stopped final Critic; one story per escalating row |
| `275d5e9` | `grit ratings unrated` says when a run has no source bands |

## What was built

### Sources and evidence

- Source bands by rule (`grit/core/source_bands.py`): T1 official, T2 rated
  HIGH, T3 rated MEDIUM or LOW, `UNRATED`, T4 aggregator, and `CONTESTED`.
  An unrated outlet is no longer treated as T4.
- Evidence is scored by rule from the best band and the number of
  independent outlets (methodology 13D). A finding that rests only on
  unrated, T4 or contested sources is downgraded from INCLUDE to WATCH.
- R&A owns the ratings and the contested list in
  `config/source-ratings.json`: `grit ratings set|confirm|contest|uncontest|unrated`.

### Events and reasoning

- Escalating rows are chosen by the reviewed significance score (8 or more,
  or an immediate threat), not by novelty.
- Reports of one story merge: by shared headline words, by the same
  jurisdiction and risk, and after drafting by the EventAssessor's item
  labels (`event_merges` in the run config). A same-event report that a
  selection cites can lead the row if its band is better.
- Exposure, not confirmed impact, decides relevance. Agents receive the
  business area's jurisdictions and can add risk links from the closed
  register. The Critic challenges missing and unsupported links.
- The Proposer drafts options for every key risk with a development.

### Report

- Order follows methodology section 15 and Wilson's v2: exposure snapshot,
  what changed, look-ahead, escalating items, prioritisation, coverage,
  jurisdictions, the reader's sections, named watch, recommended action
  posture, quality check, source register. Audit material is in an appendix.
- The action posture gives one principal ask, the RED rule, RED candidates
  with the R&A decision, and follow-ups by owner.
- The corrections ledger (methodology section 22) carries R&A corrections
  into every later report and every agent call. A retired phrase fails the
  Reporting contract.
- `grit scorecard --pack PACK --date DATE` checks a finished report against
  seven rules.

### Internal exposure and RED

- A weekly exposure snapshot (sites, travel, projects, financial, alerts) is
  admitted with `--internal-evidence FILE --evidence-approved-by PROFILE`. See
  [the snapshot prompt](../templates/exposure-snapshot-prompt.md) and
  [the example](../templates/exposure-snapshot.example.json). No real snapshot
  has been supplied yet.
- RED needs a named confirmer in `GRIT_RED_CONFIRMERS` and one of ten
  triggers: `grit red list|triggers|confirm|reject`.

### Search

- Thirteen official-source queries were added to
  `config/urs/URS-Scan-Plan.json` (Federal Reserve, MAS, State Department,
  MOFCOM and others).
- Searches logged in `output/runs/<run>/searches.jsonl` are replayed into a
  database that lacks their pages. A fresh database therefore needs no new
  search.

## Known weaknesses

These are the main sources of the remaining gap with Wilson's report.

1. **No internal exposure figures.** Wilson's report quotes staff counts,
   distances and Taiwan cash figures. GRIT-Scan needs R&A's weekly snapshot.
2. **Unrated sources.** 66 of the 88 sources cited in validation 15 have no
   rating. R&A must rate the most-used outlets.
3. **Row judgment.** The Saudi story still takes two rows: their item
   labels share only one distinctive word. Two minor industry items
   (Singapore Rail Academy, Hitachi's Chennai Metro section) scored 8 and
   appear as escalating.
4. **Unanswered requests.** Five management requests have no matching signal.
5. **Scenarios.** Every scenario shows "no signpost seen" because the
   register's KEY-05 signposts cover only sanctions and compliance. R&A
   should add conflict and staff-safety signposts to
   `config/urs/URS-Scenarios.json`.
6. **Model.** Every call uses `gemini-3.8-flash`. `gemini-3.8-pro` returns
   404 in the GCP project. The planned A/B test on Reporting and the final
   Critic has therefore not run.

## Suggested next work

- Improve label merging so that the two Saudi rows join, for example by
  comparing places and actors, not only words.
- Add a relevance check so minor industry or opportunity items are not
  escalated at significance 8.
- Build an offline replay harness that reruns recorded model replies against
  new code. Today, a report-stage change is checked with
  `grit resume-report --refresh` (about 4 minutes). A pool change needs a
  full run (about 20 minutes).
- Drop junk pages with broken titles, such as "Aa".

## How to work on it

Run the full test suite (820+ tests, about 2.5 minutes):

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
```

Run a validation on a fresh database. Searches come from the search log, so
no new search is sent:

```bash
GRIT_DB=/tmp/grit-urs-validation-N.sqlite .venv/bin/python -m grit weekly --pack urs --date 2026-11-02 --reasoned --fast --countersearch-limit 20 --no-submit
```

Rebuild only the reports of a finished run, then score them:

```bash
GRIT_DB=/tmp/grit-urs-validation-N.sqlite .venv/bin/python -m grit resume-report --refresh --pack urs --date 2026-11-02 --no-submit
GRIT_DB=/tmp/grit-urs-validation-N.sqlite .venv/bin/python -m grit scorecard --pack urs --date 2026-11-02
```

Keep these in mind:

- Every run writes to `output/runs/2026-11-02-urs/`, whichever database it
  uses. A validation therefore overwrites the reports there.
- Do not edit report modules while a run is live: they are imported late and
  pick up the edits.
- `output/grit.sqlite` has no reasoned run for 2 November. The latest
  reasoned data is in `/tmp/grit-urs-validation-15.sqlite`.
- The specs that describe this work are
  [44 Brief format v2](../../specs/44_Brief_Format_v2.md),
  [45 Weekly agent reasoning and critique](../../specs/45_Weekly_Agent_Reasoning_and_Critique.md)
  and [36 Key risks and the Proposer](../../specs/36_Key_Risks_Implications_and_Proposer.md).
