"""Source bands by rule (URS methodology, section 11). R&A owns the outlet ratings and the contested list in
config/source-ratings.json. The model never picks a band.

| Band | Rule |
|---|---|
| T1 | Official or primary: a government, military or intergovernmental domain, or a register source of that class. |
| T2 | An outlet rated HIGH. |
| T3 | An outlet rated MEDIUM or LOW. |
| UNRATED | An outlet R&A has not banded yet. Verify before relying on it. |
| T4 | An aggregator, tracker or prediction market. |
| CONTESTED | An outlet R&A lists as contested. A labeled signal, never support for a fact. |
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

BANDS = ("T1", "T2", "T3", "UNRATED", "T4", "CONTESTED")
LEGEND = ("T1 official or primary; T2 an outlet rated HIGH; T3 an outlet rated MEDIUM or LOW; UNRATED an outlet R&A has not "
          "banded yet, to verify; T4 an aggregator or tracker; CONTESTED an outlet R&A lists as contested, never the sole "
          "support for a fact.")
OFFICIAL_CLASSES = frozenset({"GOVERNMENT", "OFFICIAL_GOVERNMENT", "INTERGOVERNMENTAL", "REGULATOR", "CENTRAL_BANK", "EXCHANGE"})
_OFFICIAL = re.compile(r"(?:^|\.)(?:gov|mil|int)(?:\.[a-z]{2})?$|(?:^|\.)gov\.[a-z]{2,3}$|\.gc\.ca$|europa\.eu$|\.go\.[a-z]{2}$")
# Bands that can support a fact on their own, and bands that never count toward evidence.
SUPPORTING = frozenset({"T1", "T2", "T3"})
UNUSABLE = frozenset({"T4", "CONTESTED"})


def host_of(url: str | None, publisher: str | None = None) -> str:
    try:
        host = urlparse(str(url or "")).hostname or ""
    except ValueError:
        host = ""
    return (host or str(publisher or "")).lower().removeprefix("www.")


def _within(host: str, domains: frozenset[str]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def contested_domains(shared: Mapping[str, Any] | None = None) -> frozenset[str]:
    """The R&A contested list. None reads config/source-ratings.json."""
    from grit.ontology.matchers.credibility_lookup import shared_ratings

    document = shared_ratings() if shared is None else shared
    return frozenset(str(entry.get("domain") or "").lower().removeprefix("www.")
                     for entry in document.get("contested") or [] if isinstance(entry, Mapping) and entry.get("domain"))


def source_band(url: str | None, rating: str | None, publisher: str | None = None, *, source_class: str | None = None,
                contested: frozenset[str] | None = None) -> str:
    from grit.core.publisher_family import AGGREGATORS, registrable

    host = host_of(url, publisher)
    if _within(host, contested_domains() if contested is None else contested):
        return "CONTESTED"
    if _OFFICIAL.search(host) or str(source_class or "").upper() in OFFICIAL_CLASSES:
        return "T1"
    if registrable(host) in AGGREGATORS:
        return "T4"
    return {"HIGH": "T2", "MEDIUM": "T3", "LOW": "T3"}.get(str(rating or ""), "UNRATED")


def _independent(outlets: Sequence[Mapping[str, Any]]) -> int:
    from grit.core.priority import outlet_groups

    return len(outlet_groups([str(item.get("outlet") or item.get("url") or "") for item in outlets]))


def evidence_level(outlets: Sequence[Mapping[str, Any]]) -> int:
    """Methodology 13D, by rule. 3: approved internal evidence, or an official source corroborated by another
    independent outlet. 2: credible media or an official source, or two independent outlets. 1: a single weak source.
    0: aggregators, trackers or contested outlets only."""
    if any(item.get("internal") for item in outlets):
        return 3
    usable = [item for item in outlets if item.get("band") not in UNUSABLE]
    if not usable:
        return 0
    independent = _independent(usable)
    best = min(BANDS.index(str(item.get("band") or "UNRATED")) for item in usable)
    if best == 0 and independent >= 2:
        return 3
    if best <= 1 or independent >= 2:
        return 2
    return 1


def supports_fact(outlets: Sequence[Mapping[str, Any]]) -> bool:
    """An included fact needs internal evidence, a T1 to T3 source, or two independent unrated outlets."""
    if any(item.get("internal") or item.get("band") in SUPPORTING for item in outlets):
        return True
    return _independent([item for item in outlets if item.get("band") == "UNRATED"]) >= 2
