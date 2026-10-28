"""Who may search what, and over which runs (spec 40, section 2). Pure apart from reading the pack manifests.

A reader searches their own packs. A reader who receives a Group-level brief (Wilson, the CEO, the Chairman) searches every
pack. The aperture shapes what is shown, not what may be read: every reader is internal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from grit.settings import CONFIG_DIR

WINDOWS = {"run": "This run", "90d": "Last 90 days", "all": "All runs"}
DEFAULT_WINDOW = "90d"


def pack_labels(config_dir: Path = CONFIG_DIR) -> dict[str, dict[str, str]]:
    from grit.ontology.pack import manifests

    return {name: {"label": str(row.get("label") or name), "level": str(row.get("level") or "BA")} for name, row in manifests(config_dir).items()}


def reach(profile: Any, labels: dict[str, dict[str, str]] | None = None) -> tuple[str, ...]:
    """The packs this reader may search."""
    labels = labels if labels is not None else pack_labels()
    group = (profile.business_scope or {}).get("level") == "GROUP" or any(labels.get(pack, {}).get("level") == "GROUP" for pack in profile.briefs)
    if group:
        return tuple(sorted(labels))
    return tuple(pack for pack in profile.packs if pack in labels) or tuple(profile.packs)


@dataclass(frozen=True)
class Scope:
    profile: Any
    packs: tuple[str, ...]
    window: str = DEFAULT_WINDOW
    run_id: str | None = None  # the run on screen; the "run" window searches only this run
    today: date = field(default_factory=date.today)
    focus: str | None = None  # a condition or theme id the reader selected on the Dashboard tab
    labels: dict[str, dict[str, str]] = field(default_factory=dict, compare=False)
    screen: dict[str, Any] | None = None  # the open tab and the records on it

    @property
    def since(self) -> str | None:
        if self.window == "90d":
            return (self.today - timedelta(days=90)).isoformat()
        return None

    def label(self, pack: str) -> str:
        return self.labels.get(pack, {}).get("label", pack)

    def describe(self) -> str:
        packs = ", ".join(self.label(pack) for pack in self.packs)
        window = f"run {self.run_id}" if self.window == "run" and self.run_id else WINDOWS.get(self.window, self.window).lower()
        tab = f"; tab: {self.screen['tab']}" if self.screen and self.screen.get("tab") else ""
        return f"{self.profile.label}; packs: {packs}; window: {window}" + tab + (f"; focus: {self.focus}" if self.focus else "")


def scope_for(profile: Any, *, window: str = DEFAULT_WINDOW, run_id: str | None = None, pack: str | None = None, focus: str | None = None,
              today: date | None = None, labels: dict[str, dict[str, str]] | None = None, screen: dict[str, Any] | None = None) -> Scope:
    """pack narrows the search to one pack inside the reader's reach; a pack outside it is refused."""
    labels = labels if labels is not None else pack_labels()
    packs = reach(profile, labels)
    if pack:
        if pack not in packs:
            raise PermissionError(f"{profile.label} cannot search the {pack} pack. Reach: {', '.join(packs)}.")
        packs = (pack,)
    if window not in WINDOWS:
        raise ValueError(f"window is one of {', '.join(WINDOWS)}")
    return Scope(profile, packs, window, run_id, today or date.today(), focus, labels, screen)
