# Reader notes: profiles written from what readers say

**Status:** Steps 1 to 3 built, 20 October 2026 (specs 39 to 41). Decisions of 19 October are in section 10; the merged delivery plan is in section 11. Step 4 is not built yet.
**Purpose:** Replace the assumption that readers approve or reject individual signals. Readers write general notes in their own words. GRIT-Scan distils the notes into a reader profile written in Markdown, and the profile shapes each reader's brief and Co-Pilot answers.
**Read with:** [copilot-architecture.md](copilot-architecture.md), spec 24 (reader profiles and aperture), and `grit/profiles/memory.py`.

## 1. Why the current model has to change

Today a reader's memory is built from per-signal decisions. The reader presses Approve, Reject or Watch on a signal. `memory.py` counts those decisions against the signal's features (theme, named item, LOB, condition, outlet) and then:

- reorders signals within a tier;
- hides what the reader rejected.

That assumes executives triage individual signals. They will not. Wilson's email of 9 October is the evidence of how readers actually give feedback: a general note. It sets out the themes he watches, the sources he trusts, the horizons he cares about, and what he thinks risk managers miss. It touches no single signal, yet it says more about what he wants than a hundred clicks would.

The new model starts from that kind of note.

## 2. The cycle

```text
   Reader                     Jony (R&A)                    GRIT-Scan
   ──────                     ──────────                    ─────────
   writes a note  ─────────►  saves it as a dated note  ─►  1. Distil: proposes profile changes, each citing its note
   (email, Teams, chat                                        │
    /note, meeting)                                           ▼
                              2. Reviews and edits     ◄──  draft profile.md (a diff against the current version)
                                 profile.md, commits
                                                              │
                                                              ▼
                                                            3. Compile: front matter → settings applied by code
                                                                        body        → guidance given to the writers
                                                              │
   reads the brief  ◄──────────────────────────────────────  4. Brief and Co-Pilot answers use the profile version
                                                              │
                              5. Reads the fit report  ◄──  fit report: what the profile changed, what never fired,
                                 and asks the reader                     and what the brief still lacks
                                 what to change
                                   │
   writes the next note ◄──────────┘
```

Jony stays in the loop at steps 2 and 5. That is deliberate. You said you need to stay close to what works and what does not for each reader, and the fit report gives you something concrete to take into each conversation with a reader.

## 3. Notes

A note is anything a reader says about what they want from GRIT-Scan:

- an email;
- a Teams message;
- comments on a brief;
- your notes from a meeting;
- a line typed in the Co-Pilot as `/note …`.

Each note is saved verbatim, as one file with its date and source:

```text
secrets/readers/<profile-id>/notes/2026-10-09_email.md
secrets/readers/<profile-id>/notes/2026-10-21_meeting.md
```

Notes stay under `secrets/` because:

- they are personal;
- the Chairman's notes must not be visible to Wilson (your answer 5);
- `secrets/` is gitignored.

The Co-Pilot's `/note` command writes to the same folder for the reader who typed it.

A note is never edited after it is saved. If a reader changes their mind, that is a new note. The profile keeps track of which note it currently follows.

## 4. The profile file

Each reader has one profile, `secrets/readers/<profile-id>/profile.md`. It has two parts.

### Front matter: settings that code applies

The front matter is a small, fixed vocabulary of settings that code applies deterministically:

```yaml
---
profile: wilson
version: 3
updated: 2026-10-21
based_on: [N1, N2, N4]            # the notes this version follows
packs: [all]                      # your answer 1: Wilson and the Group readers search all packs
aperture: WIDE                    # WIDE or MATERIAL
brief:
  cadence: weekly                 # weekly or monthly
  length: short                   # short (2 pages), standard, or full
  sections: [BOTTOM_LINE, CONDITIONS, RESPONSE_OPTIONS, WATCHPOINTS]
  formats: [docx, pptx]
  show_review_status: false       # your answer 6: inferred from notes, not assumed
emphasis:                         # moves items up within their tier; never across tiers
  conditions: [STE-GC-06, STE-GC-07, STE-GC-11]
  countries: [Saudi Arabia, United Arab Emirates, Qatar, Ukraine]
  horizon: [1-5 years, 5-10 years]
de-emphasis:                      # moves items down within their tier; hides nothing
  topics: [ESG reporting]
sources:
  trust: [understandingwar.org, csis.org, rusi.org, trendforce.com, bis.gov]
  distrust: []
copilot:
  web_searches_per_day: 17        # your answer 3
  answer_length: short
---
```

### Body: guidance in the reader's own words

The body is prose, with fixed headings:

- **Role and the decisions they make.**
- **What they want to know.**
- **What they do not need.**
- **How they like it written:** length, tables or prose, forecasts, options.
- **Standing questions:** things they ask every month.
- **Open questions for the reader:** what the notes leave unclear, for Jony to raise next time.

Every line in the body cites the note it came from, such as "(N2, 21 Oct)". A line with no note is a guess, and the distiller is not allowed to write one.

### What a profile may and may not do

The front matter can only express what code knows how to apply. It cannot express an instruction to drop evidence. The body reaches only the model writers (the Reporter and the Co-Pilot), and only for emphasis, depth and tone.

These hold whatever a profile says:

| A profile can | A profile cannot |
|---|---|
| Choose packs, cadence, length, sections and formats | Hide a P1 or RED signal |
| Move items up or down **within** a tier | Move an item across tiers, or change a trend, a tier or a register fact |
| Say which sources the reader trusts, so they rank first among equals | Make the model state something the evidence does not support |
| Ask for options, forecasts or a short form | Remove the citations, or switch off the answer checks |
| Tell the writers what to lead with | Change what another reader sees |

So a note like "don't bother me with Taiwan" becomes Taiwan in `de-emphasis`, not hidden. A RED alert on Taiwan still reaches that reader. If a reader really wants a topic gone, Jony raises it with them as an open question; the profile does not remove it silently.

## 5. Distilling notes into a profile

A new agent, the **Profiler**, proposes changes to `profile.md`. It writes nothing on its own authority.

1. **Input:** the current profile, the new notes (N1, N2, …), and the allowed front-matter vocabulary (the conditions, countries, sections and sources that exist).
2. **Output:** a proposed new version of the profile, as a diff. Every changed line cites a note. Anything the notes leave unclear goes under "Open questions for the reader", not into a setting.
3. **Checks:**
   - Every front-matter value must exist: a real condition id, country, section or outlet domain.
   - Every body line cites a note, and its key words appear in that note.
   - No setting breaks the rules in section 4.
   - Inferred settings, such as `show_review_status`, need a note that supports them. Otherwise they keep their default and become an open question.
4. **You review it,** edit the file, and save it with a new version number. The Profiler's reasoning goes to the execution log, as it does for the Proposer.

The Profiler runs only when a new note arrives. It costs one or two model calls.

## 6. Compiling the profile

The compiler is deterministic. It reads `profile.md` and produces two things.

- **Settings.** The front matter becomes the `Profile` object that the brief, routing and Co-Pilot already use. It covers packs, aperture, sections, formats, cadence and the web budget, plus a new `emphasis` mapping. A missing key keeps today's default, so existing profiles go on working.
- **Guidance.** The body becomes a short block added to the Reporter's bottom-line prompt and to the Co-Pilot's system prompt, headed "This reader's stated preferences". The model treats it as guidance on emphasis and form, and the checks still apply to everything it writes.

### Emphasis replaces approve and reject

Ordering still works within tiers, as memory does today. The difference is where the preference comes from: the profile, not decision counts.

| Today (memory) | Proposed (emphasis) |
|---|---|
| +1 per approval of a signal with the feature, −1 per rejection, capped at ±3 | +2 for a feature listed under `emphasis`, −2 under `de-emphasis`, +1 for a trusted source |
| Hides a rejected signal, and an outlet rejected three times | Hides nothing. `distrust` puts an outlet's items last within their tier and marks them in the brief. |
| Reason: "you rejected 3 signals from this outlet" | Reason: "emphasised in your profile (N2): semiconductor prices" |

Every reorder carries its reason and the note behind it, so a reader can see why an item sits where it does, and you can trace any surprise back to a note.

The Approve, Reject and Watch buttons and the `feedback` table stay in the code, switched off by default. A reader who likes them can have them back by a profile setting.

## 7. The fit report: working out what works

This is the part that keeps you close to each reader. After each brief, GRIT-Scan writes a fit report for each reader to `secrets/readers/<profile-id>/fit/<run-id>.md`. Only you see it.

| Section | Content |
|---|---|
| Profile in force | The version and the notes it follows |
| What the profile changed | Which items emphasis moved up or down, and by which line. The items that would have led the brief without the profile. |
| Lines that never fired | Emphasis on a condition with no signal this period, or a trusted source that published nothing. These are candidates to drop, or to fill with new sources. |
| What the reader asked | The topics of the reader's Co-Pilot questions since the last brief, as counts, not the questions themselves. A topic they ask about often but that the profile does not emphasise is a candidate to add. |
| Gaps | Sections that came out thin, and standing questions the brief did not answer |
| Questions to raise | The profile's open questions, and anything above worth asking the reader |

"What the reader asked" uses only topic counts. That respects your answer 5: the reader's own questions stay private, while their interests still feed back.

## 8. A worked start: Wilson

Wilson's email of 9 October is note N1, so his profile does not start from nothing. Most of that email is Copilot's answer, which Wilson forwarded with his own covering lines. So the settings below are what he chose to pass on, not his own words. The first open question in his profile is whether they reflect his view.

Section 5's rules give these settings, each traceable to the email:

| Setting | From N1 |
|---|---|
| Emphasis on the 12 dashboard themes, monthly | "I would monitor 12 themes monthly" |
| Emphasis on Saudi Arabia, the UAE, Qatar and Ukraine | Tiers 2 and 3 of his source list, and section 3 on Ukraine |
| Trusted sources: ISW, CSIS, Atlantic Council, RUSI, TrendForce, SemiAnalysis, BIS, OFAC, CISA, NIST and others | His tiers 1 to 7 |
| Horizons of 1 to 5 years and 5 to 10 years, as well as current signals | "Layer 2 … Layer 3" |
| Russia stays a compliance watchpoint, never an opportunity | "I would not frame Russia as a normal market opportunity" |
| Sections should show the opportunity, the counter-indicators and the management decision | "For each signal, require: … Counter-indicators" |
| Open question: does he want ESG and climate in the radar at all? | "the risks/opportunities … are not climate or ESG", but climate is in his ten-domain universe |

The Chairman and the CEO have no notes yet, so their profiles keep today's defaults until their first note. They are marked "no notes yet" in the fit report, as a prompt for you.

## 9. Effect on other parts of GRIT-Scan

| Area | Change |
|---|---|
| `config/profiles/*.json` | Kept for the settings the profile does not cover. The compiled `profile.md` overrides it key by key. |
| `grit/profiles/memory.py` | Replaced by `emphasis.py`. Memory stays available behind a setting. |
| Dashboard | The Approve, Reject and Watch buttons are switched off by default. A "Profile in force" line shows the version and the notes it follows. |
| Co-Pilot | Adds `/note`. Its system prompt includes the compiled guidance. Its web budget comes from the profile. |
| Package review | Unchanged: `ReviewGate()` still approves or returns a whole package. |
| AIRIS handoff | An approved package sends to AIRIS its tier 1 and 2 members that link to the register (DIRECT, PERIMETER, or COUNTERPARTY). The BA head's package approval covers them. Per-signal approval is no longer needed. |

## 10. Decisions of 19 October 2026

| # | Question | Decision |
|---|---|---|
| 1 | AIRIS | An approved package sends its register-linked tier 1 and 2 members to AIRIS. |
| 2 | Note channel | Jony collects and saves the notes. No shared mailbox or Teams channel for now. |
| 3 | Hiding | No profile hides an item. The rule applies to every profile, the Chairman's included. |

## 11. Merged delivery plan with the Co-Pilot

The profile work and the Co-Pilot ([copilot-architecture.md](copilot-architecture.md)) share their foundations, so they are delivered as one plan, profile first.

- **The Co-Pilot reads the profile.** Its scope, emphasis, web budget and review label all come from the compiled profile. Building the Co-Pilot first would mean building those settings twice.
- **The profile needs the Co-Pilot to close its loop.** `/note` is a Co-Pilot command. The fit report counts the topics a reader asks the Co-Pilot about.
- **Each step is useful on its own.** Step 1 changes the briefs Wilson already receives, so the profile idea can be tested with him before the Co-Pilot exists.

| Step | Scope | Rough effort | What you can test |
|---|---|---|---|
| 1. Profile foundation | Notes folders; the `profile.md` format and compiler; emphasis replacing per-signal memory; the AIRIS rule; Wilson's profile from N1; "Profile in force" on the dashboard | 3 to 5 days | Wilson's next brief, ordered by his profile, with the reasons shown |
| 2. Co-Pilot core | The right-hand drawer; the state, condition-history, signal, library and register tools; scope by packs; the answer shape and checks; private logging; `/note`; all settings read from the profile | 1 to 2 weeks | Questions from Wilson and the Chairman over all runs, the briefs and the State of the Situation documents |
| 3. The loop and the web | The Profiler agent; the fit report; `web_search` with 17 a day per reader and Currents first near Brave's limit; "Add to pool" | 1 to 1.5 weeks | A note becomes a reviewed profile change; the first fit reports; questions about events after the last run |
| 4. Daily lane and nightly index | Feeds, event clustering, digests, retention, and `whats_new` | 1 to 2 weeks | Answers on what is new since yesterday |

Step 4 waits until the readers' notes show which daily sources they need (Co-Pilot decision 4). The other steps do not depend on it.

Before step 1 starts:
- the notes, profiles and fit reports go under `secrets/readers/`, which I can read and write, unlike `config/`;
- you save Wilson's 9 October email as his first note.
