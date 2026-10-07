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

    @property
    def label(self) -> str:
        """How the profile is named on a screen or a brief: the person, then the role."""
        return f"{self.name} ({self.role})" if self.name else self.role

    @property
    def receives_red(self) -> bool:
        return bool(self.red_alerts.get("receive"))

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


def load_profiles(directory: Path = PROFILES_DIR) -> dict[str, Profile]:
    profiles: dict[str, Profile] = {}
    for path in sorted(directory.glob("*.json")):
        profile = _profile(json.loads(path.read_text(encoding="utf-8")), path)
        if profile.profile_id in profiles:
            raise ValueError(f"Duplicate profile id {profile.profile_id}")
        profiles[profile.profile_id] = profile
    return profiles


def load_profile(profile_id: str, directory: Path = PROFILES_DIR) -> Profile:
    profiles = load_profiles(directory)
    if profile_id not in profiles:
        raise KeyError(f"Unknown profile '{profile_id}'. Known: {', '.join(sorted(profiles))}")
    return profiles[profile_id]


def ba_heads(profiles: dict[str, Profile]) -> list[Profile]:
    return [profile for profile in profiles.values() if profile.review_role == "BA_HEAD"]
