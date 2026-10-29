"""Leading indicators for the scenario tracker (improvement point 21).

Each indicator watches a named development and gives a band: green (no action), amber (BAs review exposure and plans) or
red (BAs act on the scenario's planned responses).

| Indicator | Measure | Green | Amber | Red |
|---|---|---|---|---|
| PLA activity | Most PLA aircraft sorties in one day, from Taiwan MND's daily reports | up to 20 | 21 to 45 | over 45 |
| Shipping and insurance | Kept signals in the window | 0 | 1 to 2 | 3 or more |
| Export controls and sanctions | Kept signals in the window | up to 2 | 3 to 5 | 6 or more |
| Cross-strait politics | Kept signals in the window | up to 5 | 6 to 12 | 13 or more |
| Overseas fab ramp-up | Kept signals in the window | up to 3 | 4 to 8 | 9 or more |

The bounds are PROPOSED by GRIT-Scan for Wilson to confirm. The PLA bounds follow MND's own reporting: a quiet day has fewer
than 20 sorties, and the large exercises of 2022 to 2024 brought 45 to over 150. Every band is printed with its status, so a
reader sees that a proposed bound is not yet a governed one. When Wilson confirms them, set BOUNDS_STATUS to CONFIRMED in a
commit that names him and the date. A measure with no reading in the window, such as a week with no MND report, has
no band: "no reading".

A signal counts for an indicator when its title or lead contains one of the indicator's phrases, or two of its single words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

SIGNALS, PLA_SORTIES = "signals", "pla_sorties_max"
PROPOSED, CONFIRMED = "PROPOSED", "CONFIRMED"
BOUNDS_STATUS = PROPOSED
NO_READING = "no reading"
_SORTIES = re.compile(r"(\d+) PLA aircraft sorties?|(\d+) sorties? of PLA aircraft", re.I)
MEASURES = {SIGNALS: "kept signals in the window", PLA_SORTIES: "most PLA aircraft sorties in one day (Taiwan MND)"}


@dataclass(frozen=True)
class LeadingIndicator:
    indicator_id: str
    name: str
    keywords: tuple[str, ...]
    applies_to: tuple[str, ...]
    green_max: int | None = None
    amber_max: int | None = None
    measure: str = SIGNALS
    action: str = ""


LEADING = (
    LeadingIndicator("PLA-ACTIVITY", "PLA activity", ("People's Liberation Army", "PLA aircraft", "PLA sortie", "PLAN ship", "PLA activities", "median line", "ADIZ incursion"),
                     ("STE-GC-05", "SOTS-CHINA-TAIWAN"), 20, 45, PLA_SORTIES,
                     "Red: Taiwan-linked BAs check staff safety plans, shipping routes and supplier stock for Taiwan."),
    LeadingIndicator("SHIPPING-INSURANCE", "Shipping and insurance", ("war risk premium", "war-risk premium", "hull insurance", "shipping insurance", "strait transit", "rerouted ships"),
                     ("STE-GC-05", "SOTS-CHINA-TAIWAN"), 0, 2, SIGNALS,
                     "Red: BAs shipping through the Taiwan Strait or the Red Sea review routes, lead times and insurance cover."),
    LeadingIndicator("EXPORT-CONTROLS", "Export controls and sanctions", ("export control", "Entity List", "OFAC sanctions", "sanctions list", "export ban"),
                     ("STE-SHIFT-06", "STE-GC-11", "SOTS-TECH-BLOCS"), 2, 5, SIGNALS,
                     "Red: BAs screen named counterparties and controlled items against the new lists."),
    LeadingIndicator("CROSS-STRAIT", "Cross-strait politics", ("cross-strait", "Taiwan Strait", "Taiwan Affairs Office", "Mainland Affairs Council"),
                     ("STE-GC-05", "SOTS-CHINA-TAIWAN"), 5, 12, SIGNALS,
                     "Red: URS Taiwan reviews the Worst case's planned responses with its BA head."),
    LeadingIndicator("FAB-RAMP", "Overseas fab ramp-up", ("overseas fab", "wafer fab", "foundry ramp", "TSMC Arizona", "TSMC Kumamoto", "TSMC Dresden"),
                     ("STE-SHIFT-03", "STE-GC-06", "STE-GC-07", "SOTS-SEMIS"), 3, 8, SIGNALS,
                     "Red: BAs dependent on Taiwan chips review second sources and the timing of the shift."),
)


def band(count: int | None, green_max: int | None, amber_max: int | None) -> str:
    """green at or under green_max, amber at or under amber_max, and red above that. A missing bound is not established."""
    if green_max is None or amber_max is None or amber_max < green_max:
        return "not established"
    if count is None:
        return NO_READING
    if count <= green_max:
        return "green"
    if count <= amber_max:
        return "amber"
    return "red"


def pla_sorties(text: str) -> int | None:
    """The PLA aircraft sortie count stated in an MND daily report, or None."""
    match = _SORTIES.search(text or "")
    return int(match.group(1) or match.group(2)) if match else None


def score_indicators(rows: Sequence[Mapping[str, Any]], indicators: Sequence[LeadingIndicator] = LEADING) -> list[dict[str, Any]]:
    """For each indicator: the matching kept signals in the window, its reading, and the band with its bounds' status."""
    scored = []
    for item in indicators:
        matched = [row for row in rows if row.get("signal_id") and _hit(f"{row.get('title') or ''}\n{row.get('text') or ''}", item.keywords)]
        ids = [row["signal_id"] for row in matched]
        reading: int | None = len(ids)
        evidence = ids
        if item.measure == PLA_SORTIES:
            counts = [(count, row["signal_id"]) for row in matched if (count := pla_sorties(str(row.get("title") or ""))) is not None]
            reading = max(counts)[0] if counts else None
            evidence = [max(counts)[1]] if counts else []
        scored.append({
            "id": item.indicator_id, "name": item.name, "signals": len(ids), "signal_ids": ids,
            "measure": MEASURES[item.measure], "reading": reading, "reading_signal_ids": evidence,
            "band": band(reading, item.green_max, item.amber_max), "green_max": item.green_max, "amber_max": item.amber_max,
            "bounds_status": BOUNDS_STATUS if item.green_max is not None and item.amber_max is not None else None,
            "action": item.action, "applies_to": list(item.applies_to),
        })
    return scored


def band_text(item: Mapping[str, Any]) -> str:
    """The band as a report prints it, with the bounds and whether they are proposed or confirmed."""
    if item.get("green_max") is None or item.get("amber_max") is None:
        return "not established (no bounds set)"
    bounds = f"green up to {item['green_max']}, amber up to {item['amber_max']}"
    status = "proposed bounds, for Wilson to confirm" if item.get("bounds_status") != CONFIRMED else "confirmed bounds"
    return f"{item['band']} ({bounds}; {status})"


def indicator_line(item: Mapping[str, Any]) -> str:
    """One sentence for a report: the reading, the band with its bounds, and what red asks of the BAs."""
    reading = item.get("reading", item.get("signals"))
    measure = item.get("measure") or MEASURES[SIGNALS]
    said = f"No reading this period ({measure})." if reading is None else f"{reading}: {measure}."
    action = f" {item['action']}" if item.get("action") and item.get("band") == "red" else ""
    return f"{said} Band: {band_text(item)}.{action}"


def _hit(text: str, keywords: Sequence[str]) -> bool:
    folded = text.casefold()
    if any(" " in keyword and keyword.casefold() in folded for keyword in keywords):
        return True
    singles = [keyword for keyword in keywords if " " not in keyword and re.search(rf"(?<![a-z0-9]){re.escape(keyword.casefold())}(?![a-z0-9])", folded)]
    return len(singles) >= 2
