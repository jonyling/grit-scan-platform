"""Leading indicators for the scenario tracker.

Each indicator watches a named development. A signal counts when its title or lead contains one of the indicator's
phrases. Green, amber, and red are applied only when both bounds are set. A missing bound stays "not established".
The scan does not invent a cutoff.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class LeadingIndicator:
    indicator_id: str
    name: str
    keywords: tuple[str, ...]
    applies_to: tuple[str, ...]
    green_max: int | None = None
    amber_max: int | None = None


LEADING = (
    LeadingIndicator("PLA-ACTIVITY", "PLA activity", ("People's Liberation Army", "PLA sortie", "ADIZ incursion"), ("STE-GC-05", "SOTS-CHINA-TAIWAN")),
    LeadingIndicator("SHIPPING-INSURANCE", "Shipping and insurance", ("war risk premium", "hull insurance", "shipping insurance", "strait transit"), ("STE-GC-05", "SOTS-CHINA-TAIWAN")),
    LeadingIndicator("EXPORT-CONTROLS", "Export controls and sanctions", ("export control", "Entity List", "OFAC sanctions"), ("STE-SHIFT-06", "STE-GC-11", "SOTS-TECH-BLOCS")),
    LeadingIndicator("CROSS-STRAIT", "Cross-strait politics", ("cross-strait", "Taiwan Strait"), ("STE-GC-05", "SOTS-CHINA-TAIWAN")),
    LeadingIndicator("FAB-RAMP", "Overseas fab ramp-up", ("overseas fab", "wafer fab", "foundry ramp", "TSMC Arizona", "TSMC Kumamoto"), ("STE-SHIFT-03", "STE-GC-06", "STE-GC-07", "SOTS-SEMIS")),
)


def band(count: int, green_max: int | None, amber_max: int | None) -> str:
    """green at or under green_max, amber at or under amber_max, and red above that. A missing bound is not established."""
    if green_max is None or amber_max is None or amber_max < green_max:
        return "not established"
    if count <= green_max:
        return "green"
    if count <= amber_max:
        return "amber"
    return "red"


def score_indicators(rows: Sequence[Mapping[str, Any]], indicators: Sequence[LeadingIndicator] = LEADING) -> list[dict[str, Any]]:
    """How many kept signals in the window match each indicator, and the band when the bounds are set."""
    scored = []
    for item in indicators:
        ids = [row.get("signal_id") for row in rows if _hit(f"{row.get('title') or ''}\n{row.get('text') or ''}", item.keywords)]
        ids = [signal_id for signal_id in ids if signal_id]
        scored.append({
            "id": item.indicator_id, "name": item.name, "signals": len(ids), "signal_ids": ids,
            "band": band(len(ids), item.green_max, item.amber_max), "green_max": item.green_max, "amber_max": item.amber_max,
            "applies_to": list(item.applies_to),
        })
    return scored


def _hit(text: str, keywords: Sequence[str]) -> bool:
    folded = text.casefold()
    if any(" " in keyword and keyword.casefold() in folded for keyword in keywords):
        return True
    singles = [keyword for keyword in keywords if " " not in keyword and re.search(rf"(?<![a-z0-9]){re.escape(keyword.casefold())}(?![a-z0-9])", folded)]
    return len(singles) >= 2
