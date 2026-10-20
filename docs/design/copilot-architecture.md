# GRIT-Scan Co-Pilot: architecture proposal

**Status:** Phases 1 to 3 built, 20 October 2026 ([spec 40](../../specs/40_Copilot_Core.md), [spec 41](../../specs/41_Profiler_Fit_Report_and_Web_Search.md), [spec 42](../../specs/42_Daily_Lane_and_Nightly_Index.md)). Decisions of 19 October 2026 are in section 12. Phase 4 is the MVP.
**Purpose:** Design a chat assistant beside the GRIT-Scan dashboard that answers from the signals the program collects, searches the web when the pool is not enough, and reasons to a clear, cited answer. It must keep working as the signal pool grows every day.
**Read with:** Gemini's proposal (`Gem_GRIT-Scan_Co-Pilot_Architecture & UI_Spec.md`), and specs 35 to 38.

## 1. What the Co-Pilot must do

| Requirement | What it means in practice |
|---|---|
| Answer from the collected signals | Search every run in the reader's packs, not only the run on screen. Use the labels GRIT-Scan already computed: rung, tier, priority, conditions, named items, and trend. |
| Search the internet as a tool | When the pool is stale or silent, search the web, and show web results as unvetted, apart from pool evidence. |
| Reason, and reply cogently | Plan the question, gather evidence from the right store, then answer in a fixed shape: the answer first, then the evidence, then what it means for STE, then what is uncertain. |
| Handle a growing pool | Retrieval quality and speed must hold as signals arrive daily, for years. |
| Stay governed | Agents propose and deterministic checks decide. The chat never edits a shared fact. Every answer is cited and logged. |

GRIT-Scan already has a chat (`grit/agents/chat.py`). It answers from the selected run's shown signals: at most 12 records, chosen by embedding similarity, with citation and banned-word checks. It cannot search other runs, the briefs, the report library, the registers, or the web. It also sits under the tabs, so a long conversation pushes the dashboard off screen. This proposal replaces it and keeps its checks.

## 2. Sizing the growth problem

The concern is "an ever-growing volume". Numbers help decide how much machinery that needs.

| Measure | Today (one week, 4 packs) | Full rollout, estimated |
|---|---|---|
| Pages in the pool | 4,089 | about 30,000 a month: Brave's 2,000 queries at about 15 new pages each, plus daily feeds |
| Kept by rule | 1,403 (34%) | about 10,000 a month, or 120,000 a year |
| Vector size (768 dimensions, float32) | 12 MB | about 0.4 GB a year for kept signals, 1.1 GB for all pages |
| Report library | 2,115 passages | a few thousand more as reports are added |

At this size, an exact vector search over every kept signal from one year takes well under a second on one machine. Speed is not the main problem. The real problems are:

1. **Dilution.** One event appears as 5 to 15 near-identical pages. A plain top-k search returns the same story many times and crowds out everything else.
2. **Staleness.** A strong match from three months ago can outrank a weaker but current one.
3. **Wrong level of detail.** "Why is GPU availability escalating?" needs the condition's assessment and its trend history, not 300 raw pages.
4. **Scope.** The reader's packs, aperture and memory must apply in the chat as on the dashboard.

The design below answers each of these. Section 9 covers when to move off SQLite.

## 3. Architecture overview

```text
                       ┌──────────────────────────── Dashboard (Streamlit) ────────────────────────────┐
                       │  Tabs, KPIs, standing conditions          │  Co-Pilot drawer (right column)    │
                       │  (row click sets the chat's focus)        │  scope chips, chat, citation badges│
                       └───────────────────────────────────────────┴──────────────┬─────────────────────┘
                                                                                  │ question + context
                                                                        ┌─────────▼─────────┐
                                                                        │  Co-Pilot agent   │  plan → tools → draft → check
                                                                        │  (bounded loop)   │  (Gemini function calling)
                                                                        └─────────┬─────────┘
             ┌──────────────────┬──────────────────┬──────────────────┬───────────┴──────┬──────────────────┐
             ▼                  ▼                  ▼                  ▼                  ▼                  ▼
     State store        Event index        Library index      Register lookup     Web search        Requests
     (conditions,       (kept signals,     (signed-off SotS,  (entities,          (Brave, then      (/concern …,
     trends, briefs,    clustered into     report passages)   customers,          Currents;         proposed only)
     watchpoints)       events; hybrid)                       suppliers, LOBs)    unvetted)
             ▲                  ▲                  ▲
             └──────── Nightly index job: embed, full-text index, cluster, digest, retention ────────┘
                                 ▲
                 Weekly and monthly scans (Brave)  +  daily feed lane (RSS, Currents)
```

There are five stores. Each has its own trust level, and the answer shows which one a claim came from.

| Store | Holds | Trust | Badge in the answer |
|---|---|---|---|
| State | Condition and theme assessments per run (trend, summary, implication, options), bottom lines, watchpoints, the QA log | Validated by checks; reviewed once its package is approved | `[Brief, run date]` |
| Events | Kept signals, grouped into events, with their labels | Assessed by rule | `[Signal, outlet, date]` |
| Library | Signed-off State of the Situation documents and report passages | Report authors; Wilson's sign-off | `[Report, page]` |
| Registers | Entities, customers, suppliers, LOBs, and the typed graph | Business-area registers | `[Register]` |
| Web | Live search results | Not vetted | `[Web, unvetted, date]` |

## 4. The state store answers most questions

Gemini proposes a `condition_state_history` table. GRIT-Scan already has it. `condition_assessments` holds, for each run and each standing condition or theme:

- the trend, confidence and cited summary;
- the implication, opportunity and counter-indicator;
- the Proposer's options.

The runs table holds each bottom line. Together they are the compacted, reviewed state that Gemini's two-tier design asks for.

Executive questions are mostly about state: "what is escalating", "why", "since when", and "what should we do". For these, the agent reads the state store first, and goes to raw signals only for the evidence behind a claim. This is the main defence against dilution. It is cheap, it is the governed view, and it is already checked.

**Addition: a condition timeline view.** This is one function, `condition_history(condition, since)`. It returns the trend and summary at each run, the run where the trend changed, and the signals that drove each change. It answers "how has China–Taiwan evolved this quarter" from about 12 compact rows instead of thousands of pages.

## 5. Daily ingestion and the nightly index job

### What "daily" should mean

Daily Brave scans would spend the 2,000-query monthly limit in about a week. I would keep the weekly business-area scans and the monthly Group scan, and add a **daily feed lane** that costs nothing or very little:

- **Primary-source feeds (RSS or Atom):** BIS, OFAC, CISA, NIST, ISW, CSIS, RUSI, the UK AI Security Institute, TrendForce, and the World Bank and IMF news pages. Wilson's email names most of these as Tier 1 sources. They are free, and their provenance is better than search results.
- **Currents, daily:** one query a day for each Group standing condition and each active management request.

Every new item goes through the same deterministic assessment as a scanned page: link ladder, keep rule, tier, conditions, and RED. No model is needed, apart from one embedding per page. A daily item joins a rolling daily run per pack, such as `2026-10-18-ste-daily`. Every signal therefore keeps its run, and the existing tables and rules apply unchanged.

### The nightly index job

The job runs once a night, after the daily feed lane. Each step is deterministic and can be repeated safely.

| Step | What it does | Why |
|---|---|---|
| Embed | Embeds new pages through the existing vector cache. | Semantic search |
| Full-text index | Adds title and text to an SQLite FTS5 index (BM25). | Exact names, acronyms and figures, which embeddings handle poorly: "HBM3E", "Entity List", "RKC02" |
| Cluster into events | Joins a new kept signal to an existing event when it is the same story (cosine 0.90 or higher within 8 days, the rule the reader filter uses now), else starts a new event. An event keeps all its outlets, its earliest and latest dates, and its best-labelled signal. | Removes duplication: retrieval returns events, not pages |
| Daily digest | Per pack, records the new tier 1 and tier 2 events, new RED drafts, moved conditions, and watchpoint updates. | Answers "what's new since Monday" directly |
| Retention | Keeps kept signals and all vectors. Cuts the stored text of dropped pages to the title and first 500 characters after 90 days. | Bounds storage, and keeps the audit trail |

Dropped pages stay searchable, but only when asked: "did we see anything about X that you dropped?" Answering "why didn't GRIT-Scan pick this up?" is part of governance.

## 6. Retrieval

### Scope comes first, then search

Every search is filtered before it is ranked.

1. **The reader's packs.** A reader searches only their own packs. Wilson and the Group readers search all packs. Within that, the drawer can narrow the search to one pack.
2. **A time window.** It is taken from the question ("this week", "since June", "last quarter"). Without one, the default is the selected run plus the last 90 days.
3. **Kept signals only.** Dropped pages need an explicit request.
4. **The aperture.** A material-aperture reader searches what their brief would show. The answer states how many more matches lie outside their aperture, and offers to include them. The aperture shapes a brief; it is not an access control, because every reader is internal.
5. **The reader's profile.** The profile's emphasis reorders results within a tier; it hides nothing. P1 and RED are never pushed down. Readers write notes rather than approving signals, so the profile replaces per-signal memory ([reader-notes.md](reader-notes.md)).

### Then rank

- **Hybrid search.** BM25 (FTS5) and vector similarity each produce a ranked list, combined by reciprocal rank fusion. Fusion needs no weights to tune, and it handles exact names and paraphrases alike.
- **Event collapse.** Only the best signal of each event is kept; the event's outlet count goes in the evidence.
- **Recency and priority.** These break ties, rather than being weights in a formula. Within the fused order, newer events and higher tiers rank first. Gemini's time-decay formula needs three weights and a half-life per item type, and nobody can verify that they are right. Use it only if the simple order fails in testing.
- **Budget.** At most 12 events and 6 state rows reach the model per tool call.

## 7. The Co-Pilot agent

### A bounded loop of tools

I would use Gemini function calling directly, not LangGraph. The loop is short, and an explicit Python loop is easier to audit and test. It already matches the pattern of the other agents.

| Tool | Returns | Cost |
|---|---|---|
| `get_state(pack, run)` | Standing items with trend, summary, implication, options; the bottom line; open watchpoints | None |
| `condition_history(condition, since)` | A condition's trend and summary at each run, the turning points, and the driving signals | None |
| `search_signals(query, packs, since, until, include_dropped)` | Up to 12 events with labels, outlets and dates | One query embedding |
| `whats_new(pack, since)` | The daily digests since a date | None |
| `search_library(query, topic)` | Signed-off State of the Situation sections and report passages, with pages | One query embedding |
| `lookup_register(kind, filters)` | Entities, customers or suppliers by country, LOB or name; a graph path to STE | None |
| `web_search(query)` | Up to 8 results, each marked unvetted. Brave answers first; Currents answers first when Brave is within 10% of its monthly limit, and when Brave fails. | One search; at most 2 a question, and 17 a day per reader |
| `propose_request(kind, title, keywords)` | A proposed management request (the existing `/concern` command) | None |
| `save_note(text)` | Saves the reader's `/note` as a dated note for their profile ([reader-notes.md](reader-notes.md)) | None |

**The loop:**

1. Read the drawer context: reader, packs, aperture, run, and the selected condition, if any.
2. Plan: the model picks tools, at most 4 rounds and 2 web searches.
3. Draft the answer from the tool results.
4. Run the checks.
5. If a check fails, redraft once with the violations. If it fails again, the answer is withheld.

A question takes about 3 to 6 model calls.

### When to search the web

The model may call `web_search` whenever it judges the pool insufficient. Three deterministic triggers also make it offer a search:

- the question asks about anything after the newest signal in scope;
- the internal tools return no event;
- the question names an organisation that is in no register and no signal.

Gemini proposes a separate "sufficiency grader" model call. These triggers do the same job at no cost, and a person can predict them.

### What makes the answer cogent

Every answer has this shape, and the checks enforce it:

1. **Answer.** One to three sentences that answer the question directly.
2. **Evidence.** The facts behind it, each cited with its badge.
3. **What it means for STE.** This uses the implication and exposure that GRIT-Scan already validated. A new inference reads as a possibility, with "could", "may" or "might".
4. **What is uncertain.** What the evidence does not settle, and any counter-indicator.
5. **Next step,** where useful. For example: open the condition, read the State of the Situation, or propose a management request.

**The checks:** these are the same family as the rest of GRIT-Scan.

- Every factual sentence cites a supplied source id.
- Every number appears in the source it cites.
- A forecast names the source that makes it.
- No governed score word appears ("severity", "likelihood", "probability", "risk score", "risk rating"), and no bare URL.
- A sentence that cites only web results is marked unvetted.
- An internal report (Co-Confidential), such as the DS&C risk refresh, may be quoted to any reader of its packs, including the CEO and the Chairman, always with its access note.

The drawer shows the steps taken, such as "Searched 3 runs, 41 events; read 2 conditions; 1 web search", but not the model's private reasoning. As for the Proposer, the reasoning is stored in the execution log.

### Governance

- **No shared fact changes.** The chat never edits a shared fact. It cannot change a trend, a tier, or a register.
- **Web results never enter a brief directly.** Each web citation has an "Add to pool" button. A web page added this way enters the pool as a CHAT lane signal and goes through the deterministic assessment like any other page. It reaches a brief only if the rules keep it and a reviewer approves the package.
- **Proposals start as proposals.** A management request proposed in chat starts as PROPOSED. A reviewer activates it.
- **Every turn is logged.** The question, the context, the tool calls, the source ids, the prompt version, the hashes and the checks go to the execution log.
- **Conversations are private.** A reader's conversation is visible only to that reader. Wilson does not see the Chairman's questions, in the dashboard or in a fit report. The audit log keeps them, and nobody browses it for content.
- **Review labels follow the profile.** Whether an answer shows a "not reviewed" label is a profile setting, inferred from the reader's notes. It is not a fixed rule.

## 8. User interface

Streamlit 1.58 is installed. It supports a chat input inside a column, so I agree with Gemini's Option A: a right-hand drawer.

- **Layout.** The dashboard takes about two-thirds of the width. The Co-Pilot drawer takes the rest, with a chat history of fixed height and its own input, so the KPIs and the conditions table stay visible.
- **Scope chips.** The chips show the reader, the packs, and the time window, for example "This run / Last 90 days / All". Selecting a row of the standing-conditions table (`on_select`) sets the chat's focus to that condition and adds three starter questions: "Why did this move?", "What changed since last month?", and "What should we consider?".
- **Citation badges.** A badge for a Signal, Brief or Report opens its detail; a Web badge shows "unvetted" and "Add to pool".
- **Progress.** An `st.status` line shows the steps while the answer is prepared.
- **Fallback.** On a narrow screen, the drawer moves below the dashboard into a container of fixed height (Gemini's Option B).

## 9. Technology choices

| Concern | Proof of concept (now) | MVP (under SDD) |
|---|---|---|
| Signals and state | SQLite pool (`output/grit.sqlite`) | Dataverse as the system of record (spec 35), plus a search index |
| Full-text search | SQLite FTS5, which is already available | The search service's BM25 |
| Vector search | Exact search over a memory-mapped float32 matrix of kept-signal vectors, or the `sqlite-vec` extension | Azure AI Search (hybrid BM25 and vector, filters, security trimming, and it fits the M365 and Copilot estate), or PostgreSQL with pgvector |
| Embeddings | Gemini `gemini-embedding-001`, 768 dimensions, cached by text hash | The same, or Azure OpenAI if STE standardises on it |
| Agent model | Gemini 3.8 Flash on Vertex | The same, with a stronger model for the final answer if review shows a need |
| Scheduling | `cron` on the VM for the daily feed lane and the nightly index job | A managed scheduler |

The proof of concept stays on SQLite because Section 2 shows it is fast enough for the first one to two years of data. Its limits are concurrency (one Streamlit process; one writer at a time) and operations, not search speed. Moving to Azure AI Search or PostgreSQL is an MVP decision, taken alongside the Dataverse ledger. It should not be taken now.

## 10. Where this differs from Gemini's proposal

| Topic | Gemini | This proposal | Why |
|---|---|---|---|
| Database | Migrate to PostgreSQL with pgvector now | Stay on SQLite with FTS5 for the POC; choose Azure AI Search or pgvector for the MVP | The data fits; Dataverse is the system of record; avoid two migrations |
| Condition history | A new `condition_state_history` table | Reuse `condition_assessments`, plus a `condition_history` view | It already exists and is already checked |
| Dropped pages | Do not embed them | Embed them (already done), exclude them by default, search them on request | It costs little, and supports "why was this missed?" |
| Ranking | A weighted sum of vector, BM25 and exponential decay | Reciprocal rank fusion, then event collapse, with recency and tier as tie-breakers | No weights to tune; easier to explain |
| Duplicates | Not addressed | Events: one per story, with all its outlets | This is the main source of dilution |
| Reader preferences | A memory rule in SQL that keeps tier 1 and 2 as well as P1 and RED | A profile distilled from the reader's notes: reorders within a tier, hides nothing, never pushes down P1 or RED | Readers write notes; they do not triage signals |
| Web escalation | An LLM sufficiency grader | The model's own tool choice plus three deterministic triggers | Cheaper and predictable |
| Orchestration | LangGraph | A bounded Python loop with Gemini function calling | Short loop; easy to audit and test |
| Answer checks | Citation badges | Badges plus the full GRIT-Scan checks (citations, numbers, forecasts, score words) | The same standard as every other agent |
| Daily ingestion | Implied daily scans | A free daily feed lane; scan cadence unchanged. The feed list is drafted from Wilson's tiers and confirmed later. | The Brave budget |
| Corpora | Signals and condition state | Also the library (State of the Situation), the registers, and digests | Questions need depth and structure, not only news |

I agree with Gemini on the right-hand drawer, on injecting the sidebar context, on the run-scoped default, on badges that separate run evidence from web evidence, and on row-click focus.

## 11. Delivery in phases

| Phase | Scope | Rough effort |
|---|---|---|
| 1. Co-Pilot over the existing pool | The drawer UI; the tools `get_state`, `condition_history`, `search_signals` (FTS5 and vector, all runs in scope, memory rule), `search_library` and `lookup_register`; the bounded loop; the answer shape and checks; logging | 1 to 2 weeks |
| 2. Web | `web_search` with its badge, the daily cap, and "Add to pool" through the CHAT lane | 3 to 5 days |
| 3. Daily lane and index job | Feed connectors for the Tier 1 sources; the daily Currents queries; the nightly embed, FTS, event-clustering, digest and retention steps; `whats_new` | 1 to 2 weeks |
| 4. MVP | The search service and the Dataverse ledger, decided under SDD with the rest of the MVP | Planned with the MVP |

Phase 1 alone gives the Chairman and Wilson a useful Co-Pilot, because the state store and the library already hold most of what they ask about.

## 12. Decisions of 19 October 2026

| # | Question | Decision | Where it applies |
|---|---|---|---|
| 1 | Reach | A reader searches only their own packs. Wilson and the Group readers search all packs. | Section 6 |
| 2 | Internal reports | May be quoted to the CEO and the Chairman, with the access note. | Section 7, checks |
| 3 | Web budget | 17 searches a day for each reader. Currents goes first when Brave is near its monthly limit. | Section 7, tools |
| 4 | Daily feeds | Not put to Wilson for approval yet. Readers' notes come first: they decide what each reader needs. | Section 5; [reader-notes.md](reader-notes.md) |
| 5 | Chat records | Wilson does not see the Chairman's questions. | Section 7, governance |
| 6 | "Not reviewed" label | No fixed rule. Each reader's profile sets it, inferred from their notes. | Section 7, governance |

Decision 4 changes a basic assumption. Readers will not approve or reject signals; they will write general notes, which become a Markdown profile for each reader. [reader-notes.md](reader-notes.md) proposes how that works, and its section 11 merges the Co-Pilot phases in section 11 of this document with the profile work into one plan.
