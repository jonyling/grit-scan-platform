"""The Profiler's memory: what a reader approved and rejected, turned into a bounded preference.

Memory is deterministic. It counts the reader's latest decision on each signal against that signal's features: the themes it
hit, the register names it linked, its LOB, its standing conditions, the reader-pack items it touched, and its outlet. It
reorders signals inside one priority tier, and it hides a signal the reader rejected, or an outlet the reader rejected
repeatedly and never approved. It never moves a signal across tiers, never hides a P1 or RED signal, and never edits a
shared fact. Every effect carries a reason.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

FEATURE_CAP = 3  # one feature can move a signal by at most this much
OUTLET_REJECTIONS_TO_HIDE = 3


@dataclass(frozen=True)
class Memory:
    profile_id: str
    approved: Counter = field(default_factory=Counter)
    rejected: Counter = field(default_factory=Counter)
    decisions: dict[str, str] = field(default_factory=dict)  # signal_id -> latest decision

    @property
    def size(self) -> int:
        return len(self.decisions)

    def weight(self, feature: str) -> int:
        return max(-FEATURE_CAP, min(FEATURE_CAP, self.approved[feature] - self.rejected[feature]))

    def summary(self, top: int = 5) -> dict[str, list[tuple[str, int]]]:
        weights = {feature: self.weight(feature) for feature in set(self.approved) | set(self.rejected)}
        liked = sorted(((f, w) for f, w in weights.items() if w > 0), key=lambda item: (-item[1], item[0]))[:top]
        disliked = sorted(((f, w) for f, w in weights.items() if w < 0), key=lambda item: (item[1], item[0]))[:top]
        return {"prefers": liked, "avoids": disliked}


def outlet(record: Mapping[str, Any]) -> str:
    publisher = str(record.get("publisher") or "").strip().casefold()
    if publisher:
        return publisher.removeprefix("www.")
    return urlparse(str(record.get("url") or "")).netloc.casefold().removeprefix("www.")


def features(record: Mapping[str, Any]) -> list[str]:
    """Features of one assessed signal. Each is a typed string, so a theme id cannot collide with an entity id."""
    found: list[str] = []
    link = record.get("link") or {}
    for item in link.get("involves") or []:
        if item.get("strong") and item.get("cls") != "Place":
            found.append(f"node:{item.get('node_id')}")
    for theme in record.get("themes") or link.get("themes") or []:
        theme_id = theme.get("theme_id") if isinstance(theme, Mapping) else theme
        if theme_id:
            found.append(f"theme:{theme_id}")
    if link.get("canonical_lob"):
        found.append(f"lob:{link['canonical_lob']}")
    for condition_id in record.get("condition_ids") or []:
        found.append(f"condition:{condition_id}")
    for hit in record.get("exposure") or []:
        found.append(f"exposure:{hit.get('item_id')}")
    name = outlet(record)
    if name:
        found.append(f"outlet:{name}")
    return sorted(set(found))


def build_memory(profile_id: str, feedback: Iterable[Mapping[str, Any]], records_by_signal: Mapping[str, Mapping[str, Any]]) -> Memory:
    """feedback rows in submission order. The latest decision per signal counts. WATCH counts as neither."""
    decisions: dict[str, str] = {}
    for row in feedback:
        if row.get("profile_id") == profile_id:
            decisions[str(row["signal_id"])] = str(row["decision"])
    approved: Counter = Counter()
    rejected: Counter = Counter()
    for signal_id, decision in decisions.items():
        record = records_by_signal.get(signal_id)
        if record is None:
            continue
        target = approved if decision == "APPROVE" else rejected if decision == "REJECT" else None
        if target is not None:
            target.update(features(record))
    return Memory(profile_id, approved, rejected, decisions)


@dataclass(frozen=True)
class MemoryEffect:
    affinity: int
    hidden: bool
    reasons: tuple[str, ...]

    def as_record(self) -> dict[str, Any]:
        return {"affinity": self.affinity, "hidden": self.hidden, "reasons": list(self.reasons)}


def apply_memory(record: Mapping[str, Any], memory: Memory, *, protected: bool = False) -> MemoryEffect:
    """protected is True for a P1 or RED signal. Memory can reorder it and never hides it."""
    signal_id = str(record.get("signal_id") or "")
    own = memory.decisions.get(signal_id)
    reasons: list[str] = []
    feats = features(record)
    affinity = sum(memory.weight(feature) for feature in feats)
    moved = [f"{feature} ({memory.weight(feature):+d})" for feature in feats if memory.weight(feature)]
    if moved:
        reasons.append("Memory reorders within the tier: " + ", ".join(moved[:6]) + ".")
    hidden = False
    if own == "REJECT" and not protected:
        hidden = True
        reasons.append("Hidden: this reader rejected this signal.")
    name = outlet(record)
    if name and not protected and memory.rejected[f"outlet:{name}"] >= OUTLET_REJECTIONS_TO_HIDE and not memory.approved[f"outlet:{name}"]:
        hidden = True
        reasons.append(f"Hidden: this reader rejected {memory.rejected[f'outlet:{name}']} signals from {name} and approved none.")
    if own == "APPROVE":
        reasons.append("This reader approved this signal.")
    return MemoryEffect(affinity, hidden, tuple(reasons))
