"""Emphasis: the reader profile's preferences applied to one signal (spec 39). Pure: no I/O, no model.

Emphasis moves a signal within its priority tier and hides nothing. A condition, theme, topic or country the profile
emphasises moves it up; one the profile plays down moves it down; a trusted source breaks ties upward; a distrusted source
puts it last within its tier and says so. A P1 or RED signal is never moved down. Every effect carries a reason that names
the profile version and its notes, so a reader can see why an item sits where it does.

A reader whose profile turns on the signal buttons also has their Approve, Reject and Watch decisions counted, as the
earlier memory did, but a rejection now moves a signal down instead of hiding it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlparse

EMPHASIS, DE_EMPHASIS, TRUSTED, DISTRUSTED = 2, -2, 1, -10
CAP = 6  # emphasis and de-emphasis together move a signal at most this much
LEAD_CHARS = 400


@dataclass(frozen=True)
class Effect:
    affinity: int
    hidden: bool
    reasons: tuple[str, ...]

    def as_record(self) -> dict[str, Any]:
        return {"affinity": self.affinity, "hidden": self.hidden, "reasons": list(self.reasons)}


NO_EFFECT = Effect(0, False, ())


def _phrase(phrase: str) -> re.Pattern[str]:
    """Whole words, any spacing, and a plural: "export control" matches "export controls" and "GPU" matches "GPUs"."""
    body = r"[\s-]+".join(re.escape(part) for part in phrase.split())
    return re.compile(rf"(?<![\w]){body}(?:e?s)?(?![\w])", 0 if phrase.isupper() else re.IGNORECASE)


def _host(record: Mapping[str, Any]) -> str:
    publisher = str(record.get("publisher") or "").strip().casefold().removeprefix("www.")
    host = urlparse(str(record.get("url") or "")).netloc.casefold().removeprefix("www.")
    return host or publisher


def _from(domain: str, host: str) -> bool:
    return bool(host) and (host == domain or host.endswith(f".{domain}"))


def matches(record: Mapping[str, Any], settings: Mapping[str, Any]) -> list[str]:
    """The emphasis items a signal meets: a condition or theme it hits strongly, or a topic or country in its title or lead."""
    found: list[str] = []
    conditions = set(record.get("condition_ids") or [])
    conditions |= {theme.get("theme_id") for theme in record.get("themes") or [] if isinstance(theme, Mapping) and theme.get("strong")}
    found += [item for item in settings.get("conditions") or [] if item in conditions]
    lead = f"{record.get('title') or ''}\n{str(record.get('text') or '')[:LEAD_CHARS]}"
    for key in ("topics", "countries"):
        found += [item for item in settings.get(key) or [] if _phrase(item).search(lead)]
    return found


def profile_effect(record: Mapping[str, Any], profile: Any, *, protected: bool = False, memory: Any | None = None) -> Effect:
    """protected is True for a P1 or RED signal: emphasis can move it up and never down."""
    reader = getattr(profile, "reader", None)
    reasons: list[str] = []
    affinity = 0
    if reader is not None:
        names = reader.names
        label = reader.label
        up = matches(record, reader.section("emphasis"))
        down = matches(record, reader.section("de_emphasis"))
        moved = max(-CAP, min(CAP, EMPHASIS * len(up) + DE_EMPHASIS * len(down)))
        affinity += moved
        if up:
            reasons.append(f"Moved up by your {label}: " + ", ".join(names.get(item, item) for item in up) + ".")
        if down:
            reasons.append(f"Moved down by your {label}: " + ", ".join(names.get(item, item) for item in down) + ".")
        host = _host(record)
        sources = reader.section("sources")
        trusted = next((domain for domain in sources.get("trust") or [] if _from(domain, host)), None)
        distrusted = next((domain for domain in sources.get("distrust") or [] if _from(domain, host)), None)
        if trusted:
            affinity += TRUSTED
            reasons.append(f"From {trusted}, a source your {label} trusts.")
        if distrusted:
            affinity += DISTRUSTED
            reasons.append(f"From {distrusted}, a source your {label} distrusts: placed last within its tier.")
    if memory is not None and getattr(profile, "signal_buttons", False):
        from grit.profiles.memory import apply_memory

        remembered = apply_memory(record, memory, protected=protected)
        affinity += remembered.affinity
        reasons += [reason for reason in remembered.reasons if not reason.startswith("Hidden")]
        if memory.decisions.get(str(record.get("signal_id") or "")) == "REJECT":
            affinity -= CAP
            reasons.append("You rejected this signal: placed lower within its tier. Nothing is hidden.")
    if protected and affinity < 0:
        affinity = 0
        reasons.append("A P1 or RED signal is never moved down.")
    return Effect(affinity, False, tuple(reasons))
