"""Where ST Engineering people are, from the internal context R&A supplies (spec 46, section 2). Pure apart from reading the file.

The file is secrets/adverse/internal-context.json. It gives local staff by entity and country, and travellers and assignees by
country, each with an as-at date. A safety or security event in one of those countries is an exposure of STE people, even when
no news page names ST Engineering. Country-level only: the data names no city or site, so a match says the country, not the place.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Mapping

PEOPLE = "PEOPLE"  # the link rung of a page that matches through the staff log
PEOPLE_RISK = "PEOPLE_RISK"  # the lexicon category for threats to people
STALE_DAYS = 30  # older staff data is still used, and the report says it is stale

# Canonical country, and the names news uses for it: the country, its people, and cities a report may name alone.
ALIASES: dict[str, tuple[str, ...]] = {
    "United Arab Emirates": ("United Arab Emirates", "UAE", "U.A.E.", "Emirati", "Emirates", "Dubai", "Abu Dhabi", "Sharjah"),
    "Saudi Arabia": ("Saudi Arabia", "Saudi", "Riyadh", "Jeddah", "Dammam", "NEOM"),
    "Qatar": ("Qatar", "Qatari", "Doha", "Al Udeid", "Lusail"),
    "Israel": ("Israel", "Israeli", "Tel Aviv", "Jerusalem", "Haifa"),
    "Egypt": ("Egypt", "Egyptian", "Cairo", "Alexandria"),
    "Kuwait": ("Kuwait", "Kuwaiti"),
    "Bahrain": ("Bahrain", "Bahraini", "Manama"),
    "Oman": ("Oman", "Omani", "Muscat"),
}


def canonical_country(name: Any) -> str:
    """'UAE (Dubai)' is 'United Arab Emirates'. A name not in ALIASES is returned as written."""
    text = str(name or "").split("(")[0].strip()
    for country, names in ALIASES.items():
        if any(text.casefold() == alias.casefold() for alias in names):
            return country
    return text


def _patterns(country: str) -> list[re.Pattern[str]]:
    names = ALIASES.get(country, (country,))
    # Upper-case abbreviations such as UAE match case-sensitively, so "uae" inside a word or a sentence does not.
    return [re.compile(rf"(?<![\w.]){re.escape(alias)}(?![\w])", 0 if alias.isupper() else re.IGNORECASE) for alias in names]


def load_people(path: Path | None = None, *, today: date | None = None) -> dict[str, Any] | None:
    """The staff by country, or None when R&A supplied no file. Reads the R&A summary layout (middle_east_local_staff,
    middle_east_travellers_and_assignees, entity_level_examples) and the record layout (travel, personnel, staff_safety)."""
    from grit.settings import SECRETS_DIR

    path = path or SECRETS_DIR / "adverse" / "internal-context.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    countries: dict[str, dict[str, Any]] = {}

    def at(country: Any) -> dict[str, Any]:
        return countries.setdefault(canonical_country(country), {"local_staff": 0, "entities": [], "travellers": 0})

    dates: dict[str, str] = {}
    caveats: list[str] = []
    local = data.get("middle_east_local_staff") or {}
    for row in local.get("entries") or []:
        place = at(row.get("country"))
        place["local_staff"] += int(row.get("headcount") or 0)
        if row.get("entity") and row["entity"] not in place["entities"]:
            place["entities"].append(str(row["entity"]))
    if local:
        dates["local_staff"] = str(local.get("as_at") or "")
        caveats += [str(local[key]) for key in ("caveat",) if local.get(key)]
    travel = data.get("middle_east_travellers_and_assignees") or {}
    for country, count in (travel.get("by_country") or {}).items():
        at(country)["travellers"] += int(count or 0)
    if travel:
        dates["travellers"] = str(travel.get("as_at") or "")
        caveats += [str(travel[key]) for key in ("caveat",) if travel.get(key)]
    for row in data.get("entity_level_examples") or []:
        place = at(row.get("country"))
        if row.get("entity") and row["entity"] not in place["entities"]:
            place["entities"].append(str(row["entity"]))
    for row in data.get("personnel") or []:
        place = at(row.get("country"))
        place["local_staff"] += int(row.get("headcount") or 0)
        if row.get("entity") and row["entity"] not in place["entities"]:
            place["entities"].append(str(row["entity"]))
    for row in data.get("travel") or []:
        at(row.get("country"))["travellers"] += 1
    if data.get("asOf"):
        dates.setdefault("local_staff", str(data["asOf"]))
    countries = {name: row for name, row in countries.items() if name and (row["local_staff"] or row["travellers"] or row["entities"])}
    oldest = min((value for value in dates.values() if value), default=None)
    age = (today or date.today()) - date.fromisoformat(oldest) if oldest else None
    return {"countries": countries, "dates": dates, "age_days": age.days if age is not None else None, "stale": age is not None and age.days > STALE_DAYS,
            "caveats": caveats, "gap": data.get("gap"), "local_total": local.get("total_incl_smaller_sites"), "travellers_total": travel.get("total")}


def countries_named(text: str, people: Mapping[str, Any] | None) -> list[str]:
    """The countries with STE people that the text names, by country, people or city."""
    if not people:
        return []
    return [country for country in people["countries"] if any(pattern.search(text or "") for pattern in _patterns(country))]


def node_id(country: str) -> str:
    return "PEOPLE-" + re.sub(r"[^a-z0-9]+", "-", country.casefold()).strip("-")


def presence(country: str, people: Mapping[str, Any]) -> str:
    """'Qatar: 52 local staff (URS), as at 4 November 2026; 21 travellers and assignees, as at 30 October 2026.'"""
    from grit.adverse.render import long_date

    row = people["countries"][country]
    parts = []
    if row["local_staff"] or row["entities"]:
        staff = f"{row['local_staff']} local staff" if row["local_staff"] else "local staff, number not stated"
        entities = f" ({', '.join(row['entities'])})" if row["entities"] else ""
        parts.append(f"{staff}{entities}, as at {long_date(people['dates'].get('local_staff'))}")
    if row["travellers"]:
        parts.append(f"{row['travellers']} travellers and assignees, as at {long_date(people['dates'].get('travellers'))}")
    return f"{country}: " + "; ".join(parts) + "."
