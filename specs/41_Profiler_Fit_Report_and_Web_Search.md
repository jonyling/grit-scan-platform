# 41. Profiler, fit report and web search

**Status:** Built (Step 3 of the merged plan), 2026-10-20.
**Trigger:** The merged plan in [docs/design/reader-notes.md](../docs/design/reader-notes.md), section 11, and the Co-Pilot decisions of 19 October 2026 (web budget, Currents first near Brave's limit).
**Builds on:** spec 39 (reader notes and profiles) and spec 40 (Co-Pilot core).

## 1. The Profiler

The Profiler drafts the next version of a reader's `profile.md` from their filed notes. It changes nothing on its own: Jony reviews the proposal and accepts it.

To draft, review and accept a new version:

```bash
.venv/bin/python -m grit profile propose --profile wilson
.venv/bin/python -m grit profile accept --profile wilson
```

`propose` runs when a filed note is not yet in the profile's `based_on`. Add `--force` to propose anyway. It writes two files to `secrets/readers/<profile>/proposals/`:

| File | Content |
|---|---|
| `profile_v<N>.md` | The proposed profile. Edit it before accepting if you wish. |
| `profile_v<N>.review.md` | Each change with its note and quote, the open questions for the reader, and a diff against the current version |

`accept` checks the latest proposal again, keeps the current profile in `history/`, and puts the proposal in force.

The model returns the whole proposed file and a list of changes. Each change names the setting, the note and a verbatim quote. The checks decide:

| Check | Rule |
|---|---|
| Profile | The proposal passes `check()` (spec 39): vocabulary, filed notes, and a citation on every body line. |
| Version | `version` is one more than the current one, `updated` is today, and `based_on` names every filed note. |
| Changes | Every changed setting is covered by a listed change. Every change's quote is in the note it cites. |
| Grounding | Every topic, country and source domain the proposal adds appears in a filed note. |
| Quotations | A quotation in the body is in a note the line cites. |

Quotes match on words alone, so punctuation and broken characters in a saved email do not matter. A failing proposal is redrafted once with the violations listed, then withheld as `profile_v<N>.withheld.md`. The model's reasoning goes to the execution log, never to the profile.

## 2. The fit report

The fit report shows how well a reader's profile fitted one run's brief. It is for Jony only, in `secrets/readers/<profile>/fit/<run-id>.md`. `grit report` writes one for each reader with a brief for the pack. To write one by hand:

```bash
.venv/bin/python -m grit profile fit --profile wilson
```

Add `--run RUN_ID` for one run; the default is the latest run of each pack the reader has a brief for.

| Section | Content |
|---|---|
| Profile in force | The version and its notes, or "no notes yet" |
| What the profile changed | The priority findings with and without the profile's emphasis, and the signals it moved most, with reasons |
| Lines that never fired | Emphasis items and trusted sources with no signal in the run |
| What the reader asked | Counts of the reader's Co-Pilot questions since the previous run, by State of the Situation topic and by the profile's own items. The questions themselves never appear. |
| Gaps | Standing items without a validated assessment, named items with nothing found, and how many shown signals share the key words of each standing question |
| Questions to raise | The profile's open questions, plus candidates from the other sections. For example, a topic asked about twice that the profile does not emphasise. |

## 3. Web search

The Co-Pilot's `web_search` tool searches live news when GRIT-Scan's records are not enough.

| Rule | Behaviour |
|---|---|
| Engines | Currents answers first, to keep Brave's free monthly queries for the weekly scans; Brave answers when Currents fails or returns nothing. With `COPILOT_WEB_ORDER=brave`, Brave answers first unless its local monthly count is at 90% of `BRAVE_MONTHLY_LIMIT` (default 1,000). A Brave search from the Co-Pilot counts against the monthly guard like a scan query. |
| Budget | 17 searches a day for each reader, or `copilot.web_searches_per_day` from their profile. At most 2 for one question. Each search is a row in `copilot_web_searches`. |
| Trigger | The model may search whenever the records are silent. Two deterministic triggers also prompt it: a search of the signals that returns nothing, and a question about the present ("since", "latest", "this week", "now") when the newest signal in scope is more than two days old. |
| Trust | A web result is a `W` source. Its badge reads "Web, unvetted", with the outlet and date, linked. The answer carries the note "Web results are unvetted: GRIT-Scan's rules have not assessed them." A web result never overrides a brief's assessment; a difference goes under "What is uncertain". |
| Off switch | A toggle in the drawer, or `grit ask --no-web`. |

## 4. Add to pool

A web result enters a brief only through the pool, and only if the rules keep it.

1. The reader selects **Add to pool** under a web citation in the drawer, or Jony runs `grit pool-add`.
2. The page enters the pool as a `CHAT`-lane signal. It is queued in `pool_additions` for each pack in the answer's scope.
3. The next scan of each pack gives the page a sighting (`acquire`). The run's deterministic assessment keeps or drops it like any other page.
4. A kept page reaches a brief only when a reviewer approves the package.

To queue a page from the command line:

```bash
.venv/bin/python -m grit pool-add --profile wilson --url URL --title "TITLE"
```

`URL` and `TITLE` come from the web citation; `grit ask` prints the command for each one. `--pack` narrows the packs inside the reader's reach.

## 5. Notes from the Co-Pilot

`/note TEXT` in the drawer saves the note to `secrets/readers/<profile>/inbox/` (spec 40). `grit notes list` shows each inbox file with the `grit notes add` command that files it.

## 6. Not yet built

- The daily feed lane, the nightly index job, and `whats_new` are built in spec 42.
- Guidance from the profile body in the Reporter's prompt. The bottom line is shared by every reader of a run, so per-reader guidance needs per-reader writing, which is an MVP decision.
