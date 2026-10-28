# 43. Best, medium and worst case scenarios

**Status:** Built, 2026-10-22: the Strategist (step 1), approval with signpost keywords (step 2), the signpost tracker (step 3), the scenario outlook in the Group radar, deck and Co-Pilot (step 4), and the quarterly refresh (step 5). Wilson's approval of the first draft was relayed by Jony on 22 October 2026.
**Trigger:** Level 3 of Jony's 9 October 2026 email to Wilson: "predicted scenarios … following the BMW framework". The R&A team agreed the plan on 22 October 2026.
**Replaces:** the free-form scenarios of spec 37, section 6.

## 1. What it does

For each of Wilson's 8 strategic shifts, the Strategist proposes three cases over the next 1 to 5 years:

- **Best case for STE**;
- **Medium case for STE**;
- **Worst case for STE**.

Best and worst are judged for ST Engineering, not for the world. No case is a forecast, and no case carries odds. The signposts show which case is unfolding. Wilson reviews and edits every case before any reader sees it.

To draft the cases from a completed Group run:

```bash
.venv/bin/grit scenarios --pack ste --date 2026-10-13
```

The draft goes to `output/runs/RUN/scenarios/`, as JSON and as a Word document for review, with the status `PROPOSED_FOR_REVIEW`. To redo only the shifts whose draft was withheld, and keep the others unchanged, add `--redraft`.

## 2. What the Strategist reads

| Input | Source |
|---|---|
| The shift | The Group pack's 8 strategic shifts |
| The State of the Situation | The latest draft whose topic covers the shift (spec 38): structural drivers, where things stand, key uncertainties, and indicators to watch. History is left out. |
| Report passages | The passages that document cites, as `P1`, `P2`, … |
| News signals | Up to 8 of the run's kept signals that hit the shift strongly, as `S1`, `S2`, … |
| The Group conditions the shift touches | Their latest trend and assessment |
| Names and owners | The business areas, lines of business, entities and countries STE's registers name, and the owner roles a response may name |

## 3. What each case contains

| Part | Content |
|---|---|
| Builds on | One or two of the State of the Situation's key uncertainties, by name (for the shift as a whole) |
| Name and narrative | Two or three sentences on how it could unfold for STE, written as possibility |
| Why it is plausible | One to three lines of evidence, each citing its signals or report passages. A forecast names who makes it. |
| Signposts | Three or four observable developments, such as an announcement, a contract, a rule, a price or a launch, that would show this case is unfolding. No signpost appears in two cases. |
| Counter-indicators | One or two developments that would show it is not |
| What it could mean for STE | One to three sentences, each naming a business area, line of business, entity or country |
| Where STE could gain | One sentence, or nothing. It uses could, may, or might, names an anchor, and is marked "Hypothesis: to validate with BAs". The Strategist leaves it empty when the sources do not support a gain. |
| Planned responses | One or two actions, each with an owner and the signpost that triggers it |

## 4. Checks

A draft that fails is redrafted with its violations listed, up to three attempts, then withheld.

| Check | Rule |
|---|---|
| Cases | Exactly three: Best, Medium and Worst, in that order, with distinct names |
| Grounding | `builds_on` names key uncertainties copied from the State of the Situation |
| Evidence | Each line cites supplied ids; every number is in a cited source; a forecast (will, expected to, projected, forecast) names its source or begins "According to" |
| Possibility | Narratives and impacts use could, may or might, and no word of certainty |
| Signposts | Three or four per case, never shared between cases; one or two counter-indicators |
| Anchors | Every impact names a business area, line of business, entity or country from the registers. A gain sentence, when present, does the same and stays conditional. |
| Responses | A known owner role, and a trigger that points to one of the case's signposts |
| Text | No `[S#]` or `[P#]` mark outside evidence; no number outside the cited sources; no URL or governed score word |

The Strategist's reasoning goes to the execution log, never to the draft.

## 5. First run, 22 October 2026

On the 13 October Group run, the first pass proposed cases for 7 of the 8 shifts. Export controls and technology blocs was withheld, because a draft used numbers that no source held. `--redraft` then proposed it. All 8 shifts now have three cases, 24 in all, each built on State of the Situation drafts that Wilson has not yet signed off.

## 6. Approval and the scenario register

Once the reviewer has approved the draft, record the approval:

```bash
.venv/bin/grit scenarios-approve --pack ste --date 2026-10-13 --by "Wilson Yeo"
```

`--draft PATH` names the edited JSON when the reviewer's edits were saved to another file. The approval:

1. Asks the Keyworder for two to four keyword phrases for every signpost and counter-indicator. Every word of a phrase must come from the item's own text, and at least one phrase should name the change itself, not only the place.
2. Writes the register `config/ste/STE-Scenarios.json` with the status `REVIEWED`, the version, the reviewer and the date. The previous version is kept in `config/ste/history/`.
3. Keeps the last reviewed keywords for any item whose text is unchanged when the Keyworder's call fails, so a failed call never leaves the tracker blind.

The reviewer can edit any keyword in the register. Shifts whose draft was withheld are listed under `notReviewed`.

## 7. Signpost tracker

`grit scenarios-track --pack ste --date DATE` shows where each shift is heading for a run. The Group brief computes the same reading.

| Rule | Behaviour |
|---|---|
| Period | The pack's kept signals from the run and every run since the previous weekly or monthly run, daily runs included |
| Relevance | A signal must touch the shift: a strong hit on the shift, or a hit on a Group condition the shift covers |
| Match | The signal's title and first 800 characters name at least two of an item's keywords, or its only keyword |
| Distinctive | At least one matched keyword is used by no other item of the shift, and by no more than 8% (at least 5) of the period's shift signals. A place or programme every headline names does not show which case is unfolding. An item without a distinctive keyword needs all its keywords. |
| Lean | The case with the most signposts seen. A tie reads "mixed"; none reads "no signpost seen". The previous run's lean is shown beside it. |
| Responses | A planned response is "now triggered" when its trigger signpost is seen. The brief marks each triggered response "Suggestion for the business area to consider." |
| Where STE could gain | Copied from the reviewed case. The brief marks it "Hypothesis: to validate with BAs". |
| Leading indicators | PLA activity, shipping and insurance, export controls and sanctions, cross-strait politics, and overseas fab ramp-up. A kept signal counts when its title or lead contains one of the indicator's phrases. Green, amber, and red apply only when a named person has set both bounds. Until then the band is not established. |

Leans are counts of signposts seen, never odds. The tracker writes nothing; every brief recomputes it from the pool.

## 8. Scenario outlook in briefs and the Co-Pilot

When the Group pack has a reviewed register, the Group radar brief adds a "Scenario outlook" section right after the standing conditions:

- **Word, Markdown and Excel:** one row per shift, with the three case names, where the signposts lean, the signposts and counter-indicators seen with their sources, the responses now triggered, and where STE could gain. The same brief lists the five leading indicators and a table of each Group risk with the business-area risks under it and any LOB the register names. A blank LOB cell means the register does not name one. The crosswalk stays proposed until a named person confirms it.
- **PowerPoint:** a scenario outlook slide after the executive-summary slides. A pack with many critical items has two or three summary slides before that outlook.
- **Package:** the signals the outlook cites join the package membership, so review covers them.

The Co-Pilot's `get_scenarios` tool returns the reviewed cases, the current lean, the signposts seen with their signals, and the responses triggered. The Co-Pilot is told never to turn a lean into odds.

## 9. Quarterly refresh

Each quarter, run `grit scenarios` again on the latest Group run. When a reviewed register exists, the Strategist receives the reviewed cases and the signposts seen since. It keeps what still holds and changes only what the new evidence supports. The draft lists, for each shift, the changes from the reviewed version: renamed cases, changed narratives, and new or dropped signposts. Approving it makes the next register version.

## 10. First live results, 22 October 2026

On the 13 October run, the first keywords matched too loosely: one Middle East signpost counted 14 Saudi headlines that named only "Saudi Arabia" and "Vision 2030". The distinctive-keyword rule removed them. The tracker now shows one Middle East best-case signpost seen, and no signpost for the other shifts. A single run before the register existed is a thin base. The monthly reading becomes meaningful from the next Group run.
