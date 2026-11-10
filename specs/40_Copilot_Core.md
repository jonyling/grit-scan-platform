# 40. Co-Pilot core

**Status:** Built (Step 2 of the merged plan), 2026-10-19. Replaces the single-run chat under the dashboard.
**Trigger:** The R&A team's request of 18 October 2026 for a chat beside the dashboard, and the decisions of 19 October 2026. The design is [docs/design/copilot-architecture.md](../docs/design/copilot-architecture.md). This spec records what was built from its phase 1.

## 1. What it does

The Co-Pilot answers a reader's question from GRIT-Scan's own records: the briefs' assessed state, every run's signals, the report library, and the business-area registers. Each answer has a fixed shape and cites every claim. The Co-Pilot withholds an answer that fails its checks. It never writes a shared fact.

## 2. Scope

| Rule | Behavior |
|---|---|
| Reach | A reader searches their own packs. A reader who receives a Group-level brief, or whose profile scope is `GROUP`, searches every pack. Wilson, the CEO and the Chairman therefore search all packs. |
| Narrowing | The drawer can narrow the search to one pack inside the reach. A pack outside it is refused. |
| Window | This run, the last 90 days (default), or all runs. Only completed runs count. |
| Kept signals | Only kept signals, unless the model asks for dropped pages explicitly. |
| Aperture | A material-aperture reader sees what their brief would show. The tool result counts the matches outside the aperture. |
| Profile | The reader profile (spec 39) orders the events within a tier and hides nothing. |

## 3. Search

`grit/copilot/search.py` runs without a model, apart from one query embedding.

1. **Full text.** SQLite FTS5 (BM25, Porter stemming) over every signal's title and text. `ensure_fts()` adds new signals on first use and can be repeated.
2. **Vectors.** Cosine similarity over the stored `gemini-embedding-001` vectors of the signals in scope.
3. **Fusion.** Reciprocal rank fusion, `1 / (60 + rank)` summed over both lists. There are no weights to tune.
4. **Event collapse.** A page joins an earlier, better-ranked event when its title is the same, or its vector is 0.90 or more similar and it was published within 8 days. The event keeps all its outlets.
5. **Order.** The 12 best events, ordered by tier, then the profile's emphasis, then the fused rank.

## 4. Tools

Each tool registers what it returns as a source with a short id. The model sees ids, never URLs.

| Tool | Returns | Source id | Badge |
|---|---|---|---|
| `get_state` | A pack's latest brief: bottom line, standing items with trend, summary, implication and options, and open watchpoints. Brief citations become signal ids. | `B` | Pack brief and run date |
| `condition_history` | One standing item's trend and summary at each run, with turning points | `B` | Pack brief and run date |
| `search_signals` | Up to 12 events, with outlets, dates, tier, priority, rung and standing items, plus the newest signal date in scope | `S` | Outlet and date, linked |
| `search_library` | Report passages, and State of the Situation sections from the latest drafts | `R`, `D` | Report, edition and page; or the document and section |
| `lookup_register` | Entities, customers, suppliers or competitors by name, country or pack | `G` | The pack's register |

An internal report carries the note "Internal report (Co-Confidential): quoted for ST Engineering readers only." A State of the Situation section carries "State of the Situation draft: not yet signed off by Wilson." The renderer adds each note once when a cited source carries it.

## 5. The loop and the checks

`grit/copilot/agent.py` runs the loop:

1. The model receives the question, the reader's name, role and profile body, the scope, and the reader's last three turns as plain text.
2. Each round, the model asks for up to 4 lookups, or returns its answer. There are at most 4 rounds. After the last round, the model must answer.
3. The checks run. The model redrafts a failing answer once, with the violations listed. If the redraft fails too, the Co-Pilot withholds it.
4. The loop retries each model call twice, because the model sometimes returns broken JSON or an empty function call.

The model requests lookups as JSON instead of native function calls. A stub model can then drive the loop in tests. Every request is also logged as data.

The answer has five parts: `answer` (one to three sentences; two for `answer_length: short`), `evidence`, `implications`, `uncertain` and `next_step`. `grit/copilot/checks.py` requires:

| Check | Rule |
|---|---|
| Citation | Every sentence of the answer, evidence and implications cites supplied ids. |
| Numbers | Every number is in a cited source; in `uncertain` and `next_step`, in any supplied source. |
| Forecasts | A sentence with will, projected, expected to or a similar word names its source, or begins "According to". |
| Inference | An implication that cites no brief state (`B`) is written as a possibility: could, may or might. |
| Words | No governed score word (severity, likelihood, probability, risk score, risk rating), no URL, no link and no HTML. Answers render as Markdown without HTML, so text scraped from the web cannot inject markup. |
| Shape and length | Evidence is required when any source was read. The answer and evidence stay within the length limits. |

`nothing_found: true` (the JSON value, not a string) allows an uncited answer that says the records hold nothing on the question. That answer still carries no number, forecast, score word or URL.

## 6. Privacy and logging

- A turn is saved in `chat_messages` under the reader's own `profile_id`. The drawer and the history read only that reader's rows. Wilson never sees the Chairman's questions.
- Each answer is logged in `execution_log` as actor `CoPilot`, with the question, the scope, every lookup and its arguments, the model calls, each attempt's violations, and the cited ids. The model's private reasoning is not stored.
- A failed lookup, including one with a wrongly typed argument, returns an error to the model and never stops the turn.
- The answer footer reads "Co-Pilot answer from GRIT-Scan records. Not reviewed." To drop the label for a reader, set `copilot.show_review_status: false` in their profile.

## 7. Commands

The chat parses three commands in code. The model never sees them.

| Command | Effect |
|---|---|
| `/concern TITLE: keyword, keyword` (also `/topic`, `/trend`, `/keyword`) | Proposes a management request. A reviewer activates it. |
| `/note TEXT` | Saves the note to `secrets/readers/<profile>/inbox/`. Jony files it with `grit notes add`, so every note keeps one owner. |

To ask a question from the command line as a named reader:

```bash
.venv/bin/python -m grit ask --profile wilson "What is driving the risk to our GPU and HBM access?"
```

Add `--no-save` to keep the turn out of the reader's chat. The turn is still logged. `--window`, `--pack`, `--run` and `--focus` set the scope.

## 8. Dashboard

The dashboard fills the screen. **Show Co-Pilot** in the sidebar opens the drawer. **Co-Pilot width** sets its share of the screen, from 24 to 50 percent. With the drawer closed, the dashboard uses the full width. The drawer has:

- a window control and a pack filter;
- a fixed-height conversation;
- its own input;
- a status line showing the steps taken, such as "read a brief; searched the signals".

The Co-Pilot receives the tab the reader has open and the records on that tab. On the Dashboard, choosing a standing condition focuses the Co-Pilot on that item and offers three starter questions. On another tab, that Dashboard focus is not sent.

Tables keep short columns on one line, including Trend, Confidence, and the column titles. A long basis, title, or keyword list wraps on spaces. When the columns are wider than the panel, the table scrolls sideways.

## 9. Not yet built

- Web search, Add to pool, the Profiler and the fit report are built in spec 41.
- The daily feed lane, the nightly index job, and `whats_new` are built in spec 42.
