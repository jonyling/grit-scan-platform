# 48. Daily two-stage signal review

Every working day, two named people review the signals GRIT-Scan caught for a business area. The R&A Business Partner (BP) decides first, by 12:00 Singapore time. The Business Area Manager then decides on the BP's approvals, by 17:00. Only signals both approve reach the 18:00 report. Each decision feeds back into that reviewer's memory, which changes the order of what they see next.

The pilot covers Urban Solutions (`urs`). Hwaimay Mao is the BP. Gareth Tang is the Business Area Manager.

[Set up the daily signal review](../docs/daily-review-setup.md) is the operator guide.

## 1. The working day

Every step runs once per working day, in this order. A working day is Monday to Friday, excluding the holidays listed in `config/review/<pack>.json`.

| Time (SGT) | Step | What happens |
|---|---|---|
| 05:30 | Daily lane | `grit daily` catches and assesses signals ([spec 42](42_Daily_Lane_and_Nightly_Index.md)). This is unchanged. |
| 06:30 | `ARGUE` | Gemini writes a case for and a case against each kept signal. |
| 08:30 | `PUBLISH_BP` | GRIT writes the signals to the BP's SharePoint list. |
| 09:00 | `MAIL_BP` | GRIT emails the BP pack to the BP. |
| 12:00 | `CLOSE_BP` | The BP stage closes. Decisions are frozen. |
| 12:00 | `PUBLISH_MANAGER` | GRIT writes the BP's approvals to the Manager's list. |
| 13:00 | `MAIL_MANAGER` | GRIT emails the Manager pack to the Manager. |
| 17:00 | `CLOSE_MANAGER` | The Manager stage closes. Decisions are frozen. |
| 18:00 | `MAIL_FINAL` | GRIT emails the final pack to both. |

`grit daily-review tick` runs from cron every 15 minutes. It runs each step that is due, after the step before it is done. A failed step runs again at the next tick. A step that needs a missing setting is marked `SKIPPED`, and the day goes on. The packs are still written to `output/review/`.

If the server is down at a deadline, the stage closes at the next tick. Only decisions made by the deadline count.

## 2. Which signals a day holds

A day's review holds the kept signals from every daily run since the previous working day. Monday's review therefore holds Saturday's, Sunday's and Monday's signals. The day after a holiday holds the holiday's signals too.

- A signal kept by several daily runs appears once, with the latest assessment.
- A signal that an earlier review already published is left out.
- `maxSignals` caps the rows. A P1 or RED signal is always published, whatever the cap. Signals over the cap go to the "Over the cap" sheet of the BP workbook.

## 3. Gemini argues each signal

Agents propose. Rules check. A named person decides.

1. The Proposer writes the case that the signal matters to the business area, in at most 90 words. It quotes the page to support the case.
2. The Critic reads the case for and writes the case against, in at most 70 words. It then gives a verdict on the case for: `HOLDS`, `WEAK` or `FAILS`.
3. Rules check both drafts. Every quote must appear word for word in the page. Every number must be in the page. Score words such as severity and likelihood are refused.
4. A draft that fails the checks gets one retry, with the problems named. A draft that fails twice is withheld. The reviewer sees "Argument withheld".

Neither agent sets a tier, a score, RED, or a decision. The model is `GEMINI_MODEL`, by default `gemini-2.5-flash`. Without Gemini, every signal still goes to the reviewer, marked "Argument not available".

## 4. The SharePoint lists

Each stage has its own list. Only that stage's reviewer and the R&A owners can open it. Item-level permissions are not used.

Each row shows the headline, the source and a link, the tier, RED, Adverse, the risk level, the trend, the evidence confidence, and why the rules kept it. It also shows both of Gemini's cases, and a memory note that says how the reviewer's past decisions moved the row. The Manager's list also shows the BP's decision and comment.

`Adverse` is Yes when (a) the signal or its URL appears in the latest READY adverse scan's included items, or (b) people-risk lexicon terms hit a country where STE people are present (internal staff log via `grit.core.people`). The site owner creates the Yes/No column once per list ([daily-review-setup](../docs/daily-review-setup.md)).

The `Decision` column has an Approve and a Reject button ([decision-column.json](../docs/review/decision-column.json)). A click sets `Decision`, `DecidedBy` and `DecidedAt` in place. A reviewer can add a comment, which GRIT keeps with the decision.

GRIT reads the lists through Microsoft Graph. Nothing calls into the server. The app has `Sites.Selected` with write access to the review site only.

## 5. Deadlines and missed decisions

A decision counts when its `DecidedAt` is at or before the stage deadline. Without `DecidedAt`, the time of the row's last change counts.

- While a stage is open, a reviewer can change a decision. Setting a row back to `Pending` withdraws it.
- At the deadline, GRIT freezes every decision and locks the rows. The buttons disappear when `Locked` is `Yes`.
- A row with no decision at the deadline is recorded as `NO_DECISION`. It counts as not approved. The Manager does not see it, and it does not reach the final pack.
- A change made after the deadline is ignored, whether or not the lock holds. If a row changed after the deadline, GRIT reads its version history and uses the version that was current at the deadline.
- If GRIT cannot read a row at the deadline, the stage stays open and the close runs again at the next tick. An unread decision is never recorded as `NO_DECISION`.

## 6. The packs

Each mail carries a Word document, a PowerPoint deck and an Excel workbook. GRIT builds them from the pool and the checked arguments. No model runs at render time.

| Pack | Recipient | Contents |
|---|---|---|
| BP pack, 09:00 | The BP | Every signal in the day's review. |
| Manager pack, 13:00 | The Manager | Only the BP's approvals, with the BP's decision and comment. |
| Final pack, 18:00 | Both | Only the signals both approved, with both decisions and times. |

The final pack carries a SHA-256 hash of the approved signal IDs. The execution log records the same hash. If nothing was approved by both, the final pack says so.

A mail goes only to the `email` in each reviewer's profile. With no address or no Graph settings, the files are written and nothing is sent. Attachments over 3 MB in total go through a Graph upload session.

GRIT never sends a mail twice. If Graph gives no clear answer to a send, the step is marked `UNCERTAIN` and is not retried. The operator checks the sender's Sent Items.

A list row is never created twice either. If a create gets no clear answer, GRIT looks the row up by `SignalId` and `ReviewDate` before it tries again.

## 7. The learning loop

Each frozen decision feeds the reviewer's memory, so tomorrow's list is ordered by today's decisions.

1. At each deadline, GRIT writes each `APPROVE` and `REJECT` to the `feedback` table, under the reviewer's profile.
2. The Profiler's memory ([spec 39](39_Reader_Notes_and_Profiles.md), `grit/profiles/memory.py`) counts those decisions against each signal's features: themes, register names, LOB, standing conditions, and outlet.
3. The next day, GRIT orders each list by tier, then RED, then the reviewer's memory. The memory note on each row says why it moved.
4. The weekly brief for the same reader is ordered by the same memory.

`NO_DECISION` is not feedback. A reviewer who is away teaches the memory nothing.

Memory reorders signals within a tier. It never moves a P1 or RED signal down. It never hides a row from the daily list, because a reviewer must see every signal to decide on it. It never changes a shared fact, a score, or a RED verdict.

A reviewer's memory is on when the JSON profile has `"signalReview": true`. A `signal_buttons` setting in the reader's `profile.md` overrides it.

`grit daily-review learning --profile hwaimay gareth` writes a report for each reviewer to `output/review/learning/`. It shows the on-time rate, the approval rate, how often the Manager upheld the BP's approvals, and the features that move signals up or down.

The dashboard shows the same report to each reviewer, under **Packs and review** > **Daily signal review**. The panel also shows today's steps and the reviewer's decisions so far. It is read-only. Decisions are made in SharePoint.

## 8. Records

| Table | Contents |
|---|---|
| `review_steps` | Each step of each day, its status (`DONE`, `SKIPPED`, `FAILED` or `UNCERTAIN`), and its outcome. |
| `review_arguments` | Gemini's cases, the critic's verdict, the quotes, and any check problems. |
| `review_items` | Each signal's row in each stage, its position, its memory note, and its SharePoint item ID. |
| `review_decisions` | Each decision: stage, reviewer, decision, comment, who changed the row, when, whether it was on time, and whether it is frozen. |

`execution_log` also records every step under the actor `DailyReview.<STEP>`.

## 9. Relation to other flows

- The Thursday weekly brief runs as before.
- RED alerts go to the duty team as before ([spec 42](42_Daily_Lane_and_Nightly_Index.md)). A RED signal also appears in the review list. Its alert does not wait for the review.
- `grit review` approves a weekly package through `ReviewGate()`. `grit daily-review` is separate and does not change a package.

## 10. Not yet built

- Business areas other than Urban Solutions. To add one, write `config/review/<pack>.json`, create its two lists, and turn on `signalReview` in both reviewers' profiles.
- Singapore public holidays for 2027, which MOM has not yet published.
