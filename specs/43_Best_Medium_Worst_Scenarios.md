# 43. Best, medium and worst case scenarios

**Status:** Step 1 built, 2026-10-22: the Best–Medium–Worst Strategist. Steps 2 to 5 (sign-off, signpost tracker, radar and Co-Pilot, quarterly refresh) are planned.
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
| Anchors | Every impact names a business area, line of business, entity or country from the registers |
| Responses | A known owner role, and a trigger that points to one of the case's signposts |
| Text | No `[S#]` or `[P#]` mark outside evidence; no number outside the cited sources; no URL or governed score word |

The Strategist's reasoning goes to the execution log, never to the draft.

## 5. First run, 22 October 2026

On the 13 October Group run, the first pass proposed cases for 7 of the 8 shifts. Export controls and technology blocs was withheld, because a draft used numbers that no source held. `--redraft` then proposed it. All 8 shifts now have three cases, 24 in all, each built on State of the Situation drafts that Wilson has not yet signed off.

## 6. Next steps

| Step | Scope |
|---|---|
| 2. Review and sign-off | Wilson edits the Word draft. The reviewed cases become `config/ste/STE-Scenarios.json`, with keywords on each signpost so code can detect it. |
| 3. Signpost tracker | Each month, count the kept signals that match each case's signposts, and show which case the evidence leans to and how that changes. Counts only, never odds. |
| 4. Radar, deck and Co-Pilot | A scenario outlook in the monthly Group radar and deck; a Co-Pilot tool for "which case is this shift heading to?" |
| 5. Quarterly refresh | Redraft each quarter, with the changes shown against the reviewed version |
