"""The Profiler (spec 41): proposes the next version of a reader's profile.md from their filed notes. It writes nothing on
its own authority: the proposal goes to secrets/readers/<profile>/proposals/, and Jony accepts it with grit profile accept.

The model returns the whole proposed profile.md and a list of changes, each citing a note with a verbatim quote. The checks
decide:
- the proposal passes check() from grit.profiles.reader (vocabulary, citations in the body, filed notes);
- its version is one more than the current one, and based_on names every filed note;
- every changed setting is covered by a listed change, and every change's quote is in the note it cites;
- every topic, country and source domain the proposal adds appears in a filed note, so nothing is invented;
- every quotation in the body is in a note the line cites.
A failing proposal is redrafted once with the violations, then withheld.
"""

from __future__ import annotations

import difflib
import re
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from grit.agents.loop.llm import LoopError, call_json
from grit.core.canonical import canonical_hash
from grit.profiles.reader import (
    APERTURES, CITATION, EXTRA_SECTIONS, FORMATS, LENGTHS, OPEN_QUESTIONS, Note, ReaderProfile, Vocabulary, check, load_reader_profile, notes, readers_dir,
    split_front_matter,
)

VERSION = "profiler-v1"
MIN_QUOTE = 12
NOTE_CHARS = 40000
_WORDS = re.compile(r"[a-z0-9]+")
_QUOTED = re.compile(r'"([^"]+)"|“([^”]+)”')  # each quotation from its own opening mark to its own closing mark
MIN_BODY_QUOTE = 8

SYSTEM = f"""You are the Profiler for GRIT-Scan, ST Engineering's risk-signal tracker. You propose the next version of one
reader's profile.md from the notes they have given (emails, meeting notes, chat notes). A person reviews your proposal
before it is used. You never decide on your own authority.

Return one JSON object:
  "profile_md": the whole proposed profile.md: YAML front matter between two '---' lines, then a Markdown body.
  "changes": a list, one per setting or body point you add, change or remove, each
     {{"setting": the front-matter key path such as "emphasis.topics" or "brief.length", or "body",
       "change": what you did, in one line,
       "note": the note id it comes from, such as "N3",
       "quote": a verbatim quotation of 12 or more characters from that note that supports it}}.
  "open_questions": what the notes leave unclear, for the reviewer to ask the reader.
  "reasoning": a short account of how you read the notes. It goes to the audit log, not to the profile.

Front matter. Only these keys exist:
  profile, version, updated (YYYY-MM-DD), based_on (list of note ids),
  packs (pack ids, or [all]), aperture ({' or '.join(APERTURES)}),
  brief and briefs.<pack>: sections, add_sections, remove_sections (section ids), formats ({', '.join(sorted(FORMATS))}),
     length ({', '.join(LENGTHS)}), show_review_status (true or false),
  emphasis and de_emphasis: conditions (condition or theme ids), topics (words or phrases; 4+ letters or an acronym in
     capitals), countries,
  sources: trust and distrust (web domains such as csis.org),
  signal_buttons (true or false),
  copilot: web_searches_per_day (0 to 50), answer_length (short or standard), show_review_status (true or false).
The vocabulary in the request lists the packs, condition and theme ids, and section ids that exist. Use no other value.

Body. Keep the current profile's headings. For a first profile, use: Role and the decisions they make; What they want to know; What they do not need; How they like
it written; Standing questions; Open questions for the reader. Every body line cites its note, such as (N2) or (N1, N3),
except under "Open questions for the reader". Put a reader's own words in quotation marks only when copied exactly.

Rules:
- Start from the current profile. Keep every setting and line the notes still support, with its citations. Change only
  what a note supports, and list each change.
- A profile never hides anything. "Don't send me X" becomes X under de_emphasis, and an open question.
- Do not guess. A setting the notes do not clearly support stays as it is, and the question goes under open questions.
- A later note overrides an earlier one on the same point. Say so in the change.
- version is the "next_version" in the request; based_on lists every note id in the request; updated is "today".
- Keep the profile short: settings a reader actually asked for, and body lines that guide a writer.
"""

REDRAFT = "Your proposal failed its checks. Fix every violation listed and return the whole JSON object again."


def words(text: str) -> str:
    """Lower-case words joined by single spaces: punctuation, quote marks and broken characters do not matter."""
    return " ".join(_WORDS.findall((text or "").casefold()))


def _flat(settings: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(settings, Mapping):
        out: dict[str, Any] = {}
        for key, value in settings.items():
            out |= _flat(value, f"{prefix}.{key}" if prefix else str(key))
        return out
    return {prefix: settings}


IDENTITY = {"profile", "version", "updated", "based_on"}


def changed_settings(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    old, new = _flat(before), _flat(after)
    return sorted(key for key in set(old) | set(new) if key.split(".")[0] not in IDENTITY and old.get(key) != new.get(key))


def _in_notes(term: str, texts: Sequence[str]) -> bool:
    needle = words(term)
    return bool(needle) and any(f" {needle} " in f" {text} " for text in texts)


def check_proposal(raw: Any, *, profile_id: str, current: ReaderProfile | None, filed: Sequence[Note], known: Vocabulary, today: str, path: Path) -> tuple[list[str], ReaderProfile | None]:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("profile_md"), str):
        return ["BAD_SHAPE: return profile_md as a string, with changes, open_questions and reasoning."], None
    try:
        front, body = split_front_matter(raw["profile_md"].strip())
    except Exception as exc:
        return [f"BAD_FRONT_MATTER: {exc}"], None
    version = front.get("version")
    proposal = ReaderProfile(profile_id=str(front.get("profile") or ""), version=version if isinstance(version, int) and not isinstance(version, bool) else 0,
                             updated=str(front["updated"]) if front.get("updated") else None, based_on=tuple(str(item) for item in front.get("based_on") or []),
                             settings=front, body=body, path=path)
    problems = [f"PROFILE: {item}" for item in check(proposal, known, filed)]
    expected = (current.version if current else 0) + 1
    if proposal.version != expected:
        problems.append(f"VERSION: version must be {expected}.")
    missing = sorted({note.note_id for note in filed} - set(proposal.based_on), key=lambda item: int(item[1:]))
    if missing:
        problems.append(f"BASED_ON: based_on must list every filed note; missing {', '.join(missing)}.")
    if proposal.updated != today:
        problems.append(f"UPDATED: updated must be {today}.")
    texts = {note.note_id: words(note.text()) for note in filed}
    changes = raw.get("changes")
    if not isinstance(changes, list) or not all(isinstance(item, Mapping) for item in changes):
        problems.append("BAD_SHAPE: changes is a list of objects with setting, change, note and quote.")
        changes = []
    covered: list[str] = []
    for number, item in enumerate(changes, start=1):
        note_id, quote, setting = str(item.get("note") or ""), str(item.get("quote") or ""), str(item.get("setting") or "")
        if note_id not in texts:
            problems.append(f"CHANGE {number}: {note_id or 'no note'} is not a filed note.")
            continue
        if len(quote.strip()) < MIN_QUOTE:
            problems.append(f"CHANGE {number}: the quote needs {MIN_QUOTE} or more characters from {note_id}.")
        elif f" {words(quote)} " not in f" {texts[note_id]} ":
            problems.append(f"CHANGE {number}: '{quote[:60]}' is not in {note_id}.")
        covered.append(setting)
    for key in changed_settings(current.settings if current else {}, front):
        if not any(key == item or key.startswith(f"{item}.") or item.startswith(f"{key}.") for item in covered):
            problems.append(f"UNLISTED_CHANGE: {key} changed, but no listed change covers it. List it with its note and quote.")
    all_text = list(texts.values())
    before = _flat(current.settings) if current else {}
    for key, value in _flat(front).items():
        if not (key.endswith(".topics") or key.endswith(".countries") or key.startswith("sources.")) or not isinstance(value, list):
            continue
        old = set(before.get(key) or [])
        for term in value:
            if isinstance(term, str) and term not in old and not _in_notes(term, all_text):
                problems.append(f"UNGROUNDED: {key}: '{term}' does not appear in any filed note.")
    heading = ""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().casefold()
            continue
        if heading.startswith(OPEN_QUESTIONS):
            continue
        cited = [f"N{value}" for value in CITATION.findall(stripped)]
        for quote in (first or second for first, second in _QUOTED.findall(stripped)):
            if len(quote) < MIN_BODY_QUOTE:
                continue
            if cited and not any(f" {words(quote)} " in f" {texts.get(note_id, '')} " for note_id in cited):
                problems.append(f"QUOTE_NOT_IN_NOTE: '{quote[:60]}' is not in {', '.join(cited)}.")
    return problems, proposal


def _vocabulary_view(known: Vocabulary) -> dict[str, Any]:
    return {"packs": sorted(known.packs), "conditions_and_themes": dict(sorted(known.items.items())), "sections": sorted(known.sections | EXTRA_SECTIONS)}


def propose(
    profile: Any, *, client: Any, model: str, known: Vocabulary, root: Path | None = None, today: date | None = None, pool: Any | None = None, force: bool = False,
) -> dict[str, Any]:
    """profile: the reader's Profile from config/profiles. Returns {status, path, problems, changes}."""
    root = root or readers_dir()
    profile_id = profile.profile_id
    current = load_reader_profile(profile_id, root)
    filed = notes(profile_id, root)
    if not filed:
        return {"status": "NO_NOTES", "path": None, "problems": [], "changes": []}
    fresh = [note for note in filed if current is None or note.note_id not in current.based_on]
    if not fresh and not force:
        return {"status": "NO_NEW_NOTES", "path": None, "problems": [], "changes": []}
    today_text = (today or date.today()).isoformat()
    next_version = (current.version if current else 0) + 1
    payload = {
        "reader": {"profile": profile_id, "name": profile.name, "role": profile.role, "briefs_today": sorted(profile.briefs)},
        "current_profile": current.path.read_text(encoding="utf-8") if current else None,
        "notes": [{"id": note.note_id, "date": note.on, "source": note.source, "new": note in fresh, "text": note.text()[:NOTE_CHARS]} for note in filed],
        "vocabulary": _vocabulary_view(known), "next_version": next_version, "today": today_text,
    }
    folder = root / profile_id / "proposals"
    path = root / profile_id / "profile.md"
    attempts: list[list[str]] = []
    raw: Any = None
    try:
        raw = call_json(client, model=model, system=SYSTEM, payload=payload, retries=2, thinking="MEDIUM")
        problems, proposal = check_proposal(raw, profile_id=profile_id, current=current, filed=filed, known=known, today=today_text, path=path)
        attempts.append(problems)
        if problems:
            raw = call_json(client, model=model, system=SYSTEM, payload={**payload, "proposal": raw, "violations": problems, "instruction": REDRAFT}, retries=2, thinking="MEDIUM")
            problems, proposal = check_proposal(raw, profile_id=profile_id, current=current, filed=filed, known=known, today=today_text, path=path)
            attempts.append(problems)
    except LoopError as exc:
        return {"status": "LLM_ERROR", "path": None, "problems": [str(exc)], "changes": []}
    folder.mkdir(parents=True, exist_ok=True)
    status = "PROPOSED" if not problems else "WITHHELD"
    for stale in (folder / f"profile_v{next_version}.md", folder / f"profile_v{next_version}.withheld.md"):
        stale.unlink(missing_ok=True)  # only this attempt's file stays, so accept() never installs an older one
    target = folder / (f"profile_v{next_version}.md" if not problems else f"profile_v{next_version}.withheld.md")
    target.write_text(raw["profile_md"].strip() + "\n" if isinstance(raw, Mapping) and isinstance(raw.get("profile_md"), str) else "", encoding="utf-8")
    review = folder / f"profile_v{next_version}.review.md"
    review.write_text(review_text(raw, current, problems, next_version, fresh), encoding="utf-8")
    if pool is not None:
        pool.log(actor_type="AGENT", actor="Profiler", version=VERSION, model=model, subject=profile_id, status=status, input_hash=canonical_hash(payload),
                 output_hash=canonical_hash(raw), detail={"attempts": attempts, "reasoning": (raw or {}).get("reasoning") if isinstance(raw, Mapping) else None,
                                                          "notes": [note.note_id for note in filed]})
    return {"status": status, "path": str(target), "review": str(review), "problems": problems, "changes": list((raw or {}).get("changes") or []) if isinstance(raw, Mapping) else []}


def review_text(raw: Any, current: ReaderProfile | None, problems: Sequence[str], version: int, fresh: Sequence[Note]) -> str:
    """What the reviewer reads: the changes with their quotes, the open questions, the checks, and the diff."""
    raw = raw if isinstance(raw, Mapping) else {}
    lines = [f"# Profile v{version}: proposal for review", "", f"New notes: {', '.join(note.note_id for note in fresh) or 'none'}.", ""]
    if problems:
        lines += ["## Withheld: the proposal failed its checks twice", "", *(f"- {item}" for item in problems), ""]
    lines += ["## Changes", ""]
    for item in raw.get("changes") or []:
        if isinstance(item, Mapping):
            lines.append(f"- **{item.get('setting')}**: {item.get('change')} ({item.get('note')}: \"{item.get('quote')}\")")
    if not any(isinstance(item, Mapping) for item in raw.get("changes") or []):
        lines.append("No change listed: the proposal changes no setting.")
    lines += ["", "## Open questions for the reader", "", *(f"- {item}" for item in raw.get("open_questions") or [] if isinstance(item, str)), ""]
    old = current.path.read_text(encoding="utf-8").splitlines() if current else []
    new = str(raw.get("profile_md") or "").strip().splitlines()
    diff = list(difflib.unified_diff(old, new, f"profile v{current.version if current else 0}", f"profile v{version}", lineterm=""))
    lines += ["## Diff", "", "```diff", *diff, "```", ""]
    if not problems:
        lines += ["To accept it, edit the proposal if you wish, then run: grit profile accept --profile " + (current.profile_id if current else "PROFILE"), ""]
    return "\n".join(lines)


def accept(profile_id: str, *, known: Vocabulary, root: Path | None = None) -> dict[str, Any]:
    """Make the latest proposal the profile, after check(). The current profile is kept in history/."""
    root = root or readers_dir()
    folder = root / profile_id / "proposals"
    found = sorted(folder.glob("profile_v*.md"), key=lambda path: int(re.search(r"_v(\d+)", path.name).group(1))) if folder.is_dir() else []
    found = [path for path in found if re.fullmatch(r"profile_v\d+\.md", path.name)]
    if not found:
        raise FileNotFoundError(f"No proposal in {folder}. Run grit profile propose --profile {profile_id} first.")
    proposal_path = found[-1]
    current = load_reader_profile(profile_id, root)
    front, body = split_front_matter(proposal_path.read_text(encoding="utf-8"))
    target = root / profile_id / "profile.md"
    version = front.get("version")
    reader = ReaderProfile(profile_id=str(front.get("profile") or ""), version=version if isinstance(version, int) else 0, updated=str(front.get("updated") or "") or None,
                           based_on=tuple(str(item) for item in front.get("based_on") or []), settings=front, body=body, path=target)
    problems = check(reader, known, notes(profile_id, root))
    if current and reader.version <= current.version:
        problems.append(f"version {reader.version} is not newer than the current version {current.version}.")
    if problems:
        raise ValueError(f"{proposal_path} has {len(problems)} problem(s): " + " | ".join(problems[:8]))
    if current:
        history = root / profile_id / "history"
        history.mkdir(parents=True, exist_ok=True)
        (history / f"profile_v{current.version}.md").write_text(current.path.read_text(encoding="utf-8"), encoding="utf-8")
    target.write_text(proposal_path.read_text(encoding="utf-8"), encoding="utf-8")
    return {"profile": str(target), "version": reader.version, "previous": current.version if current else None}
