"""Reader profiles. A profile changes what a reader sees and in what order. It never changes a shared fact."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grit.settings import PROFILES_DIR

APERTURES = frozenset({"WIDE_APERTURE", "MATERIAL_APERTURE"})
REVIEW_ROLES = frozenset({"REVIEWER", "BA_HEAD", "READER"})


@dataclass(frozen=True)
class Profile:
    profile_id: str
    name: str | None
    role: str
    aperture: str
    packs: tuple[str, ...]
    business_scope: dict[str, Any]
    review_role: str
    red_alerts: dict[str, Any]
    email: str | None
    briefs: dict[str, dict[str, Any]]
    note: str = ""
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)
    reader: Any = field(default=None, compare=False, repr=False)  # the ReaderProfile distilled from the reader's notes, if any

    @property
    def label(self) -> str:
        """How the profile is named on a screen or a brief: the person, then the role."""
        return f"{self.name} ({self.role})" if self.name else self.role

    @property
    def receives_red(self) -> bool:
        return bool(self.red_alerts.get("receive"))

    @property
    def signal_buttons(self) -> bool:
        """Approve, Reject and Watch on single signals. Off unless the reader's profile turns them on."""
        return bool(self.reader is not None and self.reader.settings.get("signal_buttons"))

    @property
    def in_force(self) -> str:
        """What shapes this reader's view, for the screen and the brief."""
        if self.reader is None:
            return "No reader profile yet. The defaults apply until the reader's first note."
        return f"Reader profile v{self.reader.version}" + (f", updated {self.reader.updated}" if self.reader.updated else "") + (f", from notes {', '.join(self.reader.based_on)}" if self.reader.based_on else "") + "."

    def brief_for(self, pack: str) -> dict[str, Any] | None:
        """The brief this reader receives for a pack, or None when they receive no brief for it."""
        return self.briefs.get(pack)


def _profile(raw: dict[str, Any], source: Path) -> Profile:
    aperture = raw.get("aperture")
    if aperture not in APERTURES:
        raise ValueError(f"{source.name}: aperture must be one of {sorted(APERTURES)}")
    role = raw.get("reviewRole")
    if role not in REVIEW_ROLES:
        raise ValueError(f"{source.name}: reviewRole must be one of {sorted(REVIEW_ROLES)}")
    packs = tuple(raw.get("packs") or ())
    if not packs:
        raise ValueError(f"{source.name}: a profile needs at least one pack")
    stray = sorted(set(raw.get("briefs") or {}) - set(packs))
    if stray:
        raise ValueError(f"{source.name}: a brief for {', '.join(stray)} needs that pack in packs")
    return Profile(
        profile_id=str(raw["profileId"]),
        name=raw.get("name"),
        role=str(raw["role"]),
        aperture=aperture,
        packs=packs,
        business_scope=dict(raw.get("businessScope") or {}),
        review_role=role,
        red_alerts=dict(raw.get("redAlerts") or {}),
        email=raw.get("email"),
        briefs=dict(raw.get("briefs") or {}),
        note=str(raw.get("note") or ""),
        raw=raw,
    )


def load_profiles(directory: Path = PROFILES_DIR, readers: Path | None = None) -> dict[str, Profile]:
    """Every profile in config/profiles, each with its reader profile applied when secrets/readers holds one. A reader
    profile that fails its checks stops the load and names the file, as a broken JSON profile does."""
    from grit.profiles.reader import apply, check, load_reader_profile, notes, vocabulary

    profiles = _load_json_profiles(directory)
    known = None
    for profile_id, profile in profiles.items():
        reader = load_reader_profile(profile_id, readers)
        if reader is None:
            continue
        known = known or vocabulary(directory.parent if directory.name == "profiles" else None)
        try:
            problems = check(reader, known, notes(profile_id, readers))
        except Exception as exc:  # a shape check() did not foresee still names the file
            raise ValueError(f"{reader.path}: {type(exc).__name__}: {exc}") from exc
        if problems:
            raise ValueError(f"{reader.path} has {len(problems)} problem(s): " + " | ".join(problems[:8]))
        profiles[profile_id] = apply(profile, reader, known)
    return profiles


def _load_json_profiles(directory: Path) -> dict[str, Profile]:
    profiles: dict[str, Profile] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}.") from exc
        profile = _profile(raw, path)
        if profile.profile_id in profiles:
            raise ValueError(f"Duplicate profile id {profile.profile_id}")
        profiles[profile.profile_id] = profile
    return profiles


def load_profile(profile_id: str, directory: Path = PROFILES_DIR, readers: Path | None = None) -> Profile:
    profiles = load_profiles(directory, readers)
    if profile_id not in profiles:
        raise KeyError(f"Unknown profile '{profile_id}'. Known: {', '.join(sorted(profiles))}")
    return profiles[profile_id]


def ba_heads(profiles: dict[str, Profile]) -> list[Profile]:
    return [profile for profile in profiles.values() if profile.review_role == "BA_HEAD"]
