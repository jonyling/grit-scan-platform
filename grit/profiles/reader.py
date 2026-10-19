"""Reader notes and the reader profile that is distilled from them (design note docs/design/reader-notes.md, spec 39).

A note is what a reader says about what they want: an email, a meeting, a chat line. It is saved once, verbatim, as
secrets/readers/<profile-id>/notes/N<n>_<date>_<source>.<ext>, and never edited. The number is given when the note is filed,
so a citation such as (N1) keeps pointing at the same note.

The profile is secrets/readers/<profile-id>/profile.md: YAML front matter with settings that code applies, then a body in the
reader's own terms, where every line cites the notes behind it. A profile can choose packs, the aperture, sections, formats
and length, and move items up or down within a tier. It cannot hide an item, move one across tiers, or change a fact.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from grit.settings import ROOT

ENV_VAR = "GRIT_READERS_DIR"
NOTE_NAME = re.compile(r"^N(\d+)_(\d{4}-\d{2}-\d{2})_([a-z0-9-]+)\.(md|txt|eml)$")
CITATION = re.compile(r"\bN(\d+)\b")
LENGTHS = {"short": 5, "standard": 8, "full": 15}  # priority findings in a brief
APERTURES = {"WIDE": "WIDE_APERTURE", "MATERIAL": "MATERIAL_APERTURE"}
FORMATS = frozenset({"docx", "pptx", "xlsx"})
EXTRA_SECTIONS = frozenset({"BOTTOM_LINE", "RESPONSE_OPTIONS"})
OPEN_QUESTIONS = "open questions"
_DOMAIN = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}$")
_TOP = {"profile", "version", "updated", "based_on", "packs", "aperture", "brief", "briefs", "emphasis", "de_emphasis", "sources", "signal_buttons", "copilot"}
_BRIEF = {"sections", "add_sections", "remove_sections", "formats", "length", "show_review_status"}
_EMPHASIS = {"conditions", "topics", "countries"}
_SOURCES = {"trust", "distrust"}
_COPILOT = {"web_searches_per_day", "answer_length", "show_review_status"}


def readers_dir() -> Path:
    override = os.environ.get(ENV_VAR, "").strip()
    return Path(override).expanduser() if override else ROOT / "secrets" / "readers"


@dataclass(frozen=True)
class Note:
    note_id: str  # N1, N2, ...
    on: str  # YYYY-MM-DD
    source: str
    path: Path

    def text(self) -> str:
        return self.path.read_bytes().decode("utf-8", errors="replace")


def notes(profile_id: str, root: Path | None = None) -> list[Note]:
    folder = (root or readers_dir()) / profile_id / "notes"
    found = []
    for path in sorted(folder.glob("N*")) if folder.is_dir() else []:
        match = NOTE_NAME.match(path.name)
        if match:
            found.append(Note(f"N{int(match.group(1))}", match.group(2), match.group(3), path))
    return sorted(found, key=lambda note: int(note.note_id[1:]))


def add_note(profile_id: str, source_file: Path, *, on: date, source: str, root: Path | None = None, move: bool = False) -> Note:
    """File a note under the next number. The bytes are kept as they are."""
    if not re.fullmatch(r"[a-z0-9-]+", source):
        raise ValueError("source is one word in lower case, such as email, meeting, teams, or chat.")
    if not source_file.is_file():
        raise FileNotFoundError(source_file)
    folder = (root or readers_dir()) / profile_id / "notes"
    folder.mkdir(parents=True, exist_ok=True)
    number = max((int(note.note_id[1:]) for note in notes(profile_id, root)), default=0) + 1
    suffix = source_file.suffix.lower() if source_file.suffix.lower() in {".md", ".txt", ".eml"} else ".txt"
    target = folder / f"N{number}_{on.isoformat()}_{source}{suffix}"
    (shutil.move if move else shutil.copyfile)(str(source_file), str(target))
    return Note(f"N{number}", on.isoformat(), source, target)


@dataclass(frozen=True)
class ReaderProfile:
    profile_id: str
    version: int
    updated: str | None
    based_on: tuple[str, ...]
    settings: Mapping[str, Any]
    body: str
    path: Path
    names: Mapping[str, str] = field(default_factory=dict)  # condition and theme ids to names, for the reasons a reader sees

    @property
    def label(self) -> str:
        return f"profile v{self.version}" + (f", from {', '.join(self.based_on)}" if self.based_on else "")

    def section(self, key: str) -> Mapping[str, Any]:
        return self.settings.get(key) or {}


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    import yaml

    if not text.startswith("---"):
        raise ValueError("A profile starts with a front matter block between two '---' lines.")
    _start, front, body = re.split(r"^---\s*$", text, maxsplit=2, flags=re.MULTILINE)
    parsed = yaml.safe_load(front) or {}
    if not isinstance(parsed, dict):
        raise ValueError("The front matter is a set of 'key: value' lines.")
    return parsed, body.strip()


def load_reader_profile(profile_id: str, root: Path | None = None) -> ReaderProfile | None:
    path = (root or readers_dir()) / profile_id / "profile.md"
    if not path.is_file():
        return None
    try:
        front, body = split_front_matter(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"{path}: {exc}") from exc
    version = front.get("version")
    return ReaderProfile(
        profile_id=str(front.get("profile") or profile_id), version=version if isinstance(version, int) and not isinstance(version, bool) else 0, updated=str(front["updated"]) if front.get("updated") else None,
        based_on=tuple(str(item) for item in front.get("based_on") or []), settings=front, body=body, path=path,
    )


@dataclass(frozen=True)
class Vocabulary:
    packs: frozenset[str]
    items: Mapping[str, str]  # condition and theme ids to names
    sections: frozenset[str]


def vocabulary(config_dir: Path | None = None) -> Vocabulary:
    """What a profile may name: the packs, the standing conditions and themes in them, and the brief sections."""
    from grit.ontology.pack import load_ontology_pack, manifests
    from grit.report.docx_brief import SECTION_TITLES
    from grit.settings import CONFIG_DIR

    config = config_dir or CONFIG_DIR
    items: dict[str, str] = {}
    known = manifests(config)
    for name in known:
        pack = load_ontology_pack(config, name)
        items |= {row["id"]: row["name"] for row in (pack.baseline or {}).get("conditions") or []}
        items |= {row["themeId"]: row["themeName"] for row in (pack.themes or {}).get("themes") or []}
    return Vocabulary(frozenset(known), items, frozenset(SECTION_TITLES) | EXTRA_SECTIONS)


def _strings(value: Any, where: str, problems: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        problems.append(f"{where}: a list of words or phrases.")
        return []
    return [item.strip() for item in value]


def _unknown(found: Mapping[str, Any], allowed: Iterable[str], where: str, problems: list[str]) -> None:
    extra = sorted(set(found) - set(allowed))
    if extra:
        problems.append(f"{where}: unknown setting {', '.join(extra)}. Allowed: {', '.join(sorted(allowed))}.")


def check(reader: ReaderProfile, known: Vocabulary, filed: Iterable[Note]) -> list[str]:
    """Every problem with a profile, in plain words. An empty list means it may be applied."""
    front = reader.settings
    problems: list[str] = []
    note_ids = {note.note_id for note in filed}
    _unknown(front, _TOP, "front matter", problems)
    if reader.profile_id != reader.path.parent.name:
        problems.append(f"profile: '{reader.profile_id}' must match the folder name '{reader.path.parent.name}'.")
    if reader.version < 1:
        problems.append("version: a whole number from 1.")
    for note_id in reader.based_on:
        if note_id not in note_ids:
            problems.append(f"based_on: {note_id} is not a filed note.")
    packs = _strings(front.get("packs"), "packs", problems)
    for pack in packs:
        if pack != "all" and pack not in known.packs:
            problems.append(f"packs: unknown pack {pack}.")
    if front.get("aperture") is not None and (not isinstance(front["aperture"], str) or front["aperture"] not in APERTURES):
        problems.append(f"aperture: one of {', '.join(APERTURES)}.")
    briefs = {"brief": front.get("brief") or {}}
    per_pack = front.get("briefs") or {}
    if not isinstance(per_pack, Mapping):
        problems.append("briefs: a set of settings for each pack, such as 'briefs: {urs: {length: short}}'.")
        per_pack = {}
    for pack, settings in per_pack.items():
        if pack not in known.packs:
            problems.append(f"briefs: unknown pack {pack}.")
        briefs[f"briefs.{pack}"] = settings or {}
    for where, settings in briefs.items():
        if not isinstance(settings, Mapping):
            problems.append(f"{where}: a set of settings.")
            continue
        _unknown(settings, _BRIEF, where, problems)
        for key in ("sections", "add_sections", "remove_sections"):
            for section in _strings(settings.get(key), f"{where}.{key}", problems):
                if section not in known.sections:
                    problems.append(f"{where}.{key}: unknown section {section}.")
        for kind in _strings(settings.get("formats"), f"{where}.formats", problems):
            if kind not in FORMATS:
                problems.append(f"{where}.formats: one of {', '.join(sorted(FORMATS))}.")
        if settings.get("length") is not None and (not isinstance(settings["length"], str) or settings["length"] not in LENGTHS):
            problems.append(f"{where}.length: one of {', '.join(LENGTHS)}.")
        if settings.get("show_review_status") is not None and not isinstance(settings["show_review_status"], bool):
            problems.append(f"{where}.show_review_status: true or false.")
    chosen: dict[str, set[str]] = {}
    for key in ("emphasis", "de_emphasis"):
        settings = front.get(key) or {}
        if not isinstance(settings, Mapping):
            problems.append(f"{key}: a set of conditions, topics and countries.")
            continue
        _unknown(settings, _EMPHASIS, key, problems)
        for item in _strings(settings.get("conditions"), f"{key}.conditions", problems):
            if item not in known.items:
                problems.append(f"{key}.conditions: unknown condition or theme {item}.")
        for item in _strings(settings.get("topics"), f"{key}.topics", problems):
            if len(item) < 4 and not (item.isupper() and len(item) >= 2):
                problems.append(f"{key}.topics: '{item}' is too short to match safely. Use four or more letters, or an acronym in capitals.")
        chosen[key] = {item.casefold() for name in _EMPHASIS for item in _strings(settings.get(name), f"{key}.{name}", [])}
    both = sorted(chosen.get("emphasis", set()) & chosen.get("de_emphasis", set()))
    if both:
        problems.append(f"emphasis and de_emphasis both name: {', '.join(both)}.")
    sources = front.get("sources") or {}
    if isinstance(sources, Mapping):
        _unknown(sources, _SOURCES, "sources", problems)
        trusted = set(_strings(sources.get("trust"), "sources.trust", problems))
        distrusted = set(_strings(sources.get("distrust"), "sources.distrust", problems))
        for domain in trusted | distrusted:
            if not _DOMAIN.match(domain):
                problems.append(f"sources: '{domain}' is not a web domain such as reuters.com.")
        if trusted & distrusted:
            problems.append(f"sources: both trusted and distrusted: {', '.join(sorted(trusted & distrusted))}.")
    else:
        problems.append("sources: a set with trust and distrust.")
    copilot = front.get("copilot") or {}
    if isinstance(copilot, Mapping):
        _unknown(copilot, _COPILOT, "copilot", problems)
        searches = copilot.get("web_searches_per_day")
        if searches is not None and (not isinstance(searches, int) or not 0 <= searches <= 50):
            problems.append("copilot.web_searches_per_day: a whole number from 0 to 50.")
        if copilot.get("answer_length") is not None and (not isinstance(copilot["answer_length"], str) or copilot["answer_length"] not in {"short", "standard"}):
            problems.append("copilot.answer_length: short or standard.")
        if copilot.get("show_review_status") is not None and not isinstance(copilot["show_review_status"], bool):
            problems.append("copilot.show_review_status: true or false.")
    if front.get("signal_buttons") is not None and not isinstance(front["signal_buttons"], bool):
        problems.append("signal_buttons: true or false.")
    problems += body_problems(reader.body, note_ids)
    return problems


def body_problems(body: str, note_ids: set[str]) -> list[str]:
    """Every line of the body cites the notes it comes from, such as (N1) or (N1, N3). Lines under the open-questions heading
    may stand without a note, because a question can come from what the notes leave out."""
    problems: list[str] = []
    heading = ""
    for number, line in enumerate(body.splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        if text.startswith("#"):
            heading = text.lstrip("#").strip().casefold()
            continue
        cited = {f"N{value}" for value in CITATION.findall(text)}
        if not cited and not heading.startswith(OPEN_QUESTIONS):
            problems.append(f"body line {number}: cite the note it comes from, such as (N1): '{text[:60]}'.")
        for note_id in sorted(cited - note_ids):
            problems.append(f"body line {number}: {note_id} is not a filed note.")
    return problems


def default_brief(pack: str, profile: Any) -> dict[str, Any] | None:
    """The brief a reader profile may add for a pack the JSON profile gives no brief: today, the Group radar."""
    from grit.ontology.group_seed import PACK, radar_brief

    if pack == PACK:
        return radar_brief(profile.name or profile.role, profile.role)
    return None


def apply(profile: Any, reader: ReaderProfile, known: Vocabulary) -> Any:
    """The profile from config/profiles with the reader profile's settings applied, key by key. A key the reader profile
    leaves out keeps its value. A pack under briefs.<pack> that the JSON profile has no brief for gets the pack's default
    brief, if it has one."""
    import dataclasses

    front = reader.settings
    packs = tuple(profile.packs)
    if front.get("packs"):
        packs = tuple(sorted(known.packs)) if "all" in front["packs"] else tuple(front["packs"])
    starting = dict(profile.briefs)
    for pack in (front.get("briefs") or {}):
        if pack in starting:
            continue
        template = default_brief(pack, profile)
        if template is None:
            raise ValueError(f"{reader.path}: briefs.{pack}: {profile.label} has no {pack} brief in config/profiles, and the pack has no default brief to add.")
        if pack not in packs:
            packs = (*packs, pack)
        starting[pack] = template
    briefs = {}
    for pack, config in starting.items():
        if pack not in packs:
            continue
        merged = dict(config)
        for settings in (front.get("brief") or {}, (front.get("briefs") or {}).get(pack) or {}):
            sections = list(settings.get("sections") or merged.get("sections") or [])
            sections += [item for item in settings.get("add_sections") or [] if item not in sections]
            sections = [item for item in sections if item not in set(settings.get("remove_sections") or [])]
            merged["sections"] = sections
            if settings.get("formats"):
                merged["formats"] = list(settings["formats"])
            if settings.get("length"):
                merged["maxFindings"] = LENGTHS[settings["length"]]
            if settings.get("show_review_status") is not None:
                merged["showReviewStatus"] = bool(settings["show_review_status"])
        briefs[pack] = merged
    named = dataclasses.replace(reader, names={item: known.items[item] for key in ("emphasis", "de_emphasis") for item in (front.get(key) or {}).get("conditions") or [] if item in known.items})
    return dataclasses.replace(
        profile, packs=packs, briefs=briefs, aperture=APERTURES.get(front.get("aperture"), profile.aperture), reader=named,
    )
