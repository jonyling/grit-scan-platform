# 39. Reader notes and profiles

**Status:** Built (Step 1 of the merged plan), 2026-10-19. Replaces the per-signal memory of spec 22 as the way a reader's preferences reach the brief.
**Trigger:** The R&A team's decision of 19 October 2026: readers write general notes and do not approve or reject single signals. The design is [docs/design/reader-notes.md](../docs/design/reader-notes.md).

## 1. The cycle

1. A reader says what they want in an email, a meeting or a chat. Jony saves it as a note.
2. `grit notes add` files the note as `N1`, `N2`, and so on, byte for byte.
3. A person (later, the Profiler agent) writes the reader's `profile.md`: settings in front matter, and a body where every line cites the notes it comes from.
4. `grit profile check` lists every problem. `load_profiles()` refuses to load a profile with a problem and names the file.
5. The next brief and the dashboard apply the profile on top of the reader's JSON profile in `config/profiles/`.

## 2. Files

Notes and profiles hold what named people said. They live in `secrets/readers/`, which git ignores. Set `GRIT_READERS_DIR` to use another folder; the tests point it at an empty one.

```text
secrets/readers/
  wilson/
    profile.md
    notes/
      N1_2026-10-09_email.txt
```

A note's file name is `N<number>_<YYYY-MM-DD>_<source>.<md|txt|eml>`. The source is one lower-case word, such as `email`, `meeting` or `teams`.

## 3. Profile settings

A key the profile leaves out keeps the value in the JSON profile.

| Key | Values | Effect |
|---|---|---|
| `profile`, `version`, `updated`, `based_on` | The folder name; a whole number from 1; a date; filed note ids | Named in every reason the reader sees, such as "profile v1, from N1". |
| `packs` | Pack ids, or `[all]` | Which packs the reader is routed. A brief still needs an entry in the JSON profile. |
| `aperture` | `WIDE` or `MATERIAL` | Spec 24 aperture. |
| `brief`, `briefs.<pack>` | `sections`, `add_sections`, `remove_sections`, `formats`, `length` (`short` 5, `standard` 8, `full` 15 findings), `show_review_status` | `brief` applies to every pack; `briefs.<pack>` then applies to one. For a pack the JSON profile has no brief for, `briefs.<pack>` adds the pack's default brief; today only the Group radar has one. |
| `emphasis`, `de_emphasis` | `conditions` (condition or theme ids), `topics`, `countries` | See section 4. A topic has four or more letters, or is an acronym in capitals. |
| `sources` | `trust`, `distrust` (web domains) | See section 4. |
| `signal_buttons` | `true` or `false` (default) | Shows Approve, Reject and Watch on single signals. |
| `copilot` | `web_searches_per_day` (0 to 50), `answer_length` (`short`, `standard`), `show_review_status` | `answer_length` and `show_review_status` shape Co-Pilot answers (spec 40). `web_searches_per_day` sets the reader's daily web searches (spec 41). |

The body must cite a note on every line, for example `(N1)`. Lines under a heading that starts with "Open questions" can stand without a citation.

## 4. Emphasis

`profile_effect()` in `grit/profiles/emphasis.py` is pure. It moves a signal within its priority tier and never hides one.

| Match | Effect |
|---|---|
| Each emphasis item met | +2 |
| Each de-emphasis item met | −2 |
| Emphasis and de-emphasis together | Capped at ±6 |
| A trusted source | +1 |
| A distrusted source | −10: last in its tier, and the reason says so |
| A P1 or RED signal | Never below 0 |

A condition or theme matches when the signal carries its id, or hits the theme strongly. A topic or country matches whole words in the title and the first 400 characters, in any case, with a plural. An acronym in capitals matches only in capitals.

When `signal_buttons` is on, the reader's Approve, Reject and Watch decisions also count, as the old memory did. A rejection moves a signal down; it does not hide it.

## 5. What changed

| Area | Before | Now |
|---|---|---|
| Routed rows | `memory`, which could hide a signal | `preference`, which never hides one. `shown` is the aperture decision only. |
| Brief method | Signals hidden by memory, and the number of decisions | The profile in force, and the number of signals it reordered |
| Review label | Always on | On unless `show_review_status: false` |
| Dashboard | Memory summary, and signal buttons for everyone | "Profile in force". Signal buttons only when the profile turns them on. |
| AIRIS | Signals the BA head approved one by one, inside an approved package | The tier 1–2 members of a package the BA head approved, linked to a register (`DIRECT`, `PERIMETER` or `COUNTERPARTY`). A signal goes once. |

## 6. Commands

To file a note and check the profile:

```bash
.venv/bin/python -m grit notes add --profile wilson --file PATH --source email --date 2026-10-09 --move
.venv/bin/python -m grit notes list
.venv/bin/python -m grit profile check --profile wilson
```

`PATH` is the saved note. `profile check` exits with 1 when the profile has a problem.

## 7. Not yet built

- The Profiler, the fit report, and the web search budget `web_searches_per_day` are built in spec 41.
