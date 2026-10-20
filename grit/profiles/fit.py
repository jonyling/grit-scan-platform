"""The fit report (spec 41): how well a reader's profile fitted one run's brief, for Jony only.

It is written to secrets/readers/<profile>/fit/<run-id>.md after each brief. Sections:
- Profile in force: the version and the notes it follows, or "no notes yet".
- What the profile changed: the priority findings with and without the profile's emphasis, and the signals it moved most.
- Lines that never fired: emphasis items and trusted sources with no signal in the run.
- What the reader asked: topic counts from the reader's Co-Pilot questions since the previous run. Never the questions.
- Gaps: standing items without a validated assessment, named items with nothing found, and the profile's standing questions
  with how many shown signals share their key words.
- Questions to raise: the profile's open questions, and candidates from the sections above.
Pure apart from reading the pool and the profile: no model.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from grit.pool import Pool
from grit.profiles.emphasis import _from, _host, _phrase, matches
from grit.profiles.reader import OPEN_QUESTIONS, readers_dir

TOP_MOVED = 8
ASKED_MIN = 2  # a topic asked about this often, and not emphasised, is a candidate to add
_KEY = re.compile(r"\b(?:[A-Za-z][A-Za-z-]{4,}|[A-Z]{2,}[0-9]*)\b")  # words of five letters or more, and acronyms in capitals
_COMMON = frozenset("about which where there their would could should these those other being after before since under while what forwarded actually really words".split())


def _section_lines(body: str, start: str) -> list[str]:
    lines, inside = [], False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            inside = stripped.lstrip("#").strip().casefold().startswith(start)
            continue
        if inside and stripped:
            lines.append(stripped)
    return lines


def _short(reasons: Sequence[str], limit: int = 200) -> str:
    """The reasons, cut at the last whole sentence that fits."""
    kept: list[str] = []
    for reason in reasons:
        if len(" ".join([*kept, reason])) > limit:
            break
        kept.append(reason)
    return " ".join(kept) or reasons[0][:limit] if reasons else ""


def _findings(rows: Sequence[Mapping[str, Any]], limit: int, *, with_profile: bool) -> list[str]:
    shown = [row for row in rows if row["shown"] and (row.get("tier") or 9) <= 2]

    def key(row: Mapping[str, Any]) -> tuple:
        affinity = row["preference"]["affinity"] if with_profile else 0
        return (row["route"]["priority"] or "P9", row.get("tier") or 9, not row.get("condition_ids"), -affinity, -(row.get("corroboration") or 1))

    ordered = sorted(sorted(shown, key=lambda row: str(row.get("published_at") or ""), reverse=True), key=key)
    return [row["signal_id"] for row in ordered[:limit]]


def topics(profile: Any) -> dict[str, list[str]]:
    """Topic name -> phrases that count a question as being about it: the State of the Situation topics, plus the profile's
    own emphasis items."""
    from grit.library.catalogue import TOPICS

    found = {item.name: [item.name, *item.keywords, *item.countries] for item in TOPICS}
    reader = getattr(profile, "reader", None)
    if reader is not None:
        for key in ("emphasis", "de_emphasis"):
            for name in ("topics", "countries"):
                for term in reader.section(key).get(name) or []:
                    found.setdefault(term, [term])
    return found


def asked(pool: Pool, profile: Any, since: str | None, until: str | None) -> Counter:
    params: list[Any] = [profile.profile_id]
    where = ""
    if since:
        where += " AND created_at >= ?"
        params.append(since)
    if until:
        where += " AND created_at < ?"
        params.append(until)
    questions = [row["content"] for row in pool.rows(f"SELECT content FROM chat_messages WHERE profile_id = ? AND role = 'user'{where}", params)
                 if not row["content"].lstrip().startswith("/")]
    counts: Counter = Counter()
    for name, phrases in topics(profile).items():
        patterns = [_phrase(phrase) for phrase in phrases if len(phrase) >= 3]
        counts[name] = sum(1 for question in questions if any(pattern.search(question) for pattern in patterns))
    return Counter({name: count for name, count in counts.items() if count})


def fit_report(pool: Pool, run_id: str, profile: Any) -> str:
    from grit.report.brief import build_brief, routed_signals

    run = pool.run(run_id)
    if run is None:
        raise KeyError(f"No run {run_id}.")
    brief = build_brief(pool, run_id, profile)
    rows = routed_signals(pool, run_id, profile)
    reader = profile.reader
    limit = int((profile.brief_for(run["pack"]) or {}).get("maxFindings") or 8)
    lines = [f"# Fit report: {profile.label}, {run_id}", "", "*For Jony only. Counts, never the reader's questions.*", "", "## Profile in force", "", profile.in_force, ""]
    candidates: list[str] = []
    if reader is None:
        lines += ["The defaults applied. File the reader's first note to start a profile.", ""]
        candidates.append("No notes yet: ask the reader what they want from this brief.")
    # What the profile changed
    lines += ["## What the profile changed", ""]
    with_profile, without = _findings(rows, limit, with_profile=True), _findings(rows, limit, with_profile=False)
    titles = {row["signal_id"]: row["title"] for row in rows}
    entered = [item for item in with_profile if item not in without]
    left = [item for item in without if item not in with_profile]
    moved = sorted((row for row in rows if row["shown"] and row["preference"]["affinity"]), key=lambda row: -abs(row["preference"]["affinity"]))
    lines.append(f"The profile moved {len(moved)} of {sum(1 for row in rows if row['shown'])} shown signals within their tiers.")
    if entered or left:
        lines += ["", "Priority findings it changed:", *(f"- In because of the profile: {titles[item]}" for item in entered), *(f"- Out because of the profile: {titles[item]}" for item in left)]
    else:
        lines.append("It did not change which signals are the priority findings.")
    if moved:
        lines += ["", "Moved most:", *(f"- {row['preference']['affinity']:+d} {row['title'][:90]} ({_short(row['preference']['reasons'])})" for row in moved[:TOP_MOVED])]
    lines.append("")
    # Lines that never fired
    lines += ["## Lines that never fired", ""]
    silent: list[str] = []
    if reader is not None:
        names = reader.names
        for key in ("emphasis", "de_emphasis"):
            section = reader.section(key)
            for name in ("conditions", "topics", "countries"):
                for item in section.get(name) or []:
                    if not any(item in matches(row, {name: [item]}) for row in rows):
                        silent.append(f"{key.replace('_', '-')} {name[:-1] if name != 'countries' else 'country'}: {names.get(item, item)}")
        hosts = [_host(row) for row in rows]
        for domain in reader.section("sources").get("trust") or []:
            if not any(_from(domain, host) for host in hosts):
                silent.append(f"trusted source: {domain}")
    lines += [f"- {item}" for item in silent] or ["- None: every line matched at least one signal."]
    if silent:
        candidates.append(f"{len(silent)} profile lines matched nothing in this run. Ask whether they still matter, or whether GRIT-Scan should search their sources.")
    lines.append("")
    # What the reader asked
    previous = pool.latest_run(run["pack"], before=run["run_date"], kind="READER_SCAN")
    counts = asked(pool, profile, previous["run_date"] if previous else None, None)
    lines += ["## What the reader asked", "", f"Co-Pilot questions since {previous['run_date'] if previous else 'the start'}, by topic:"]
    lines += [f"- {name}: {count}" for name, count in counts.most_common()] or ["- No Co-Pilot question on a known topic."]
    emphasised = {str(item).casefold() for key in ("topics", "countries") for item in ((reader.section("emphasis").get(key) or []) if reader is not None else [])}
    for name, count in counts.most_common():
        if count >= ASKED_MIN and name.casefold() not in emphasised:
            candidates.append(f"Asked about {name} {count} times, and the profile does not emphasise it. Ask whether to add it.")
    lines.append("")
    # Gaps
    lines += ["## Gaps", ""]
    empty = [row["name"] for row in brief["conditions"] if row.get("status") != "VALIDATED"]
    lines.append(f"- Standing items without a validated assessment: {', '.join(empty) if empty else 'none'}.")
    named = [item for group in brief["projects"] + brief["entities"] for item in group["items"]] + brief["suppliers"] + brief["customers"]
    quiet = [item["name"] for item in named if not item.get("text")]
    lines.append(f"- Named items with nothing found: {len(quiet)} of {len(named)}.")
    if reader is not None:
        shown_text = [f"{row.get('title') or ''} {str(row.get('text') or '')[:400]}" for row in rows if row["shown"]]
        for question in _section_lines(reader.body, "standing questions"):
            keys = [_phrase(word) for word in _KEY.findall(re.sub(r"\(N\d+(?:,\s*N\d+)*\)", "", question)) if word.casefold() not in _COMMON]
            hits = sum(1 for text in shown_text if keys and sum(bool(key.search(text)) for key in keys) >= max(1, len(keys) // 2))
            lines.append(f"- Standing question, {hits} shown signals share its key words: {question}")
            if not hits:
                candidates.append(f"Nothing in this run speaks to the standing question: {question}")
    lines.append("")
    # Questions to raise
    lines += ["## Questions to raise", ""]
    open_questions = [line.lstrip("-* ").strip() for line in _section_lines(reader.body, OPEN_QUESTIONS)] if reader is not None else []
    open_questions = [item for item in open_questions if item and not item.casefold().startswith("none")]
    lines += [f"- {item}" for item in [*open_questions, *candidates]] or ["- None."]
    return "\n".join(lines).rstrip() + "\n"


def write_fit_report(pool: Pool, run_id: str, profile: Any, *, root: Path | None = None) -> Path:
    folder = (root or readers_dir()) / profile.profile_id / "fit"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{run_id}.md"
    path.write_text(fit_report(pool, run_id, profile), encoding="utf-8")
    return path
