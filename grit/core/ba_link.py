"""Whether a finding touches the business area itself: a confirmed person, programme or contract (strict link).

The principal ask must rest on such a finding. A finding linked only through a class of supplier or customer ("marine
steel and fabrication suppliers") or a third party's contract does not qualify. The links, strongest first:

- ENTITY: the text names one of the business area's registered entities.
- COUNTERPARTY: the text names a registered customer or supplier, not a class of them.
- PROJECT: the text names a project or order-book item on the register.
- PEOPLE: a threat to people (the people-risk lexicon, in the title or lead) in a country where the business area has a
  registered entity or staff in the internal staff log.
- PROGRAMME: the same threat in the country a named customer's name places it, such as Kuwait for "Kuwait Naval Force".
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

LEAD_CHARS = 1500
# Physical threats the adverse people-risk lexicon leaves out. Added here only, so the adverse screen does not change.
THREAT_TERMS = ("attack*", "killed", "injur*", "wounded", "casualt*", "unaccounted for", "missing after", "explosion*",
                "blast", "naval mine", "strikes on", "struck by", "hijack*", "seized vessel", "piracy")
_SUFFIX = re.compile(r"\s*(?:,?\s*(?:pte\.?|pvt\.?|private|co\.?|corp\.?|inc\.?|llc|ltd\.?|limited|plc|gmbh|s\.?a\.?|as|a/s|se|pjsc|holdings?)\b\.?)+\s*$", re.IGNORECASE)


def _bare(name: str) -> str:
    """'ST Engineering Marine Ltd. - Abu Dhabi Branch' is named as 'ST Engineering Marine'."""
    name = str(name or "").split(" - ")[0].split(" (")[0].strip()
    previous = None
    while previous != name:
        previous, name = name, _SUFFIX.sub("", name).strip(" ,.")
    return name


def _named(name: str, text: str) -> bool:
    name = str(name or "").strip()
    if len(name) < 4 or len(name.split()) < 2 and not name.isupper():
        return False  # a single common word ("Marine") names nothing on its own
    return bool(re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text, re.IGNORECASE))


def _countries_in(text: str, countries: Iterable[str]) -> list[str]:
    from grit.core.people import _patterns

    return [country for country in dict.fromkeys(countries) if country and any(pattern.search(text) for pattern in _patterns(country))]


def register(pack: Any, staff: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """What the business area has: entities, named counterparties, projects, and where its people are."""
    from grit.core.people import canonical_country

    exposure = getattr(pack, "exposure", None) or {}
    entities = [row for row in exposure.get("entities") or [] if row.get("entityName")]
    counterparties: list[tuple[str, str, list[str]]] = []
    for kind, key in (("customer", "customers"), ("supplier", "suppliers")):
        for row in exposure.get(key) or []:
            if row.get("counterpartyClass"):
                continue
            name = str(row.get(f"{kind}Name") or "")
            parts = [part.strip() for alias in [name, *(row.get("aliases") or [])] for part in str(alias).split(" / ") if part.strip()]
            counterparties.append((kind, name, list(dict.fromkeys([*parts, *(_bare(part) for part in parts)]))))
    projects = [str(row.get("name") or row.get("projectName") or "") for row in exposure.get("projects") or []]
    projects += [str(row.get("name") or row.get("project") or "") for row in (getattr(pack, "order_book", None) or {}).get("items") or []]
    places: dict[str, list[str]] = {}
    for row in entities:
        country = canonical_country(row.get("countryOfIncorporation"))
        if country:
            places.setdefault(country, []).append(f"{row['entityName']} (registered entity)")
    for row in [*((staff or {}).get("sites") or []), *((staff or {}).get("travel") or [])]:
        label = f"{row.get('entity') or 'travellers'} ({row.get('headcount') or row.get('travellers')} in the internal staff log)"
        places.setdefault(str(row.get("country")), []).append(label)
    programmes: dict[str, list[str]] = {}
    for kind, name, parts in counterparties:
        if kind != "customer":
            continue
        for country in _countries_in(name, _all_countries()):
            programmes.setdefault(country, []).append(name)
    return {"entities": entities, "counterparties": counterparties, "projects": [name for name in projects if name],
            "places": places, "programmes": programmes}


def _all_countries() -> list[str]:
    from grit.core.people import COUNTRY_NAMES

    return list(COUNTRY_NAMES)


def links(title: str, text: str, area: Mapping[str, Any], *, terms: Sequence[str] | None = None) -> list[str]:
    """The strict links between one finding and the business area, as reader-facing reasons. Empty when there are none."""
    from grit.core.people import people_risk_in_lead, people_risk_terms

    terms = [*(terms if terms is not None else people_risk_terms()), *THREAT_TERMS]
    whole = f"{title or ''}\n{str(text or '')[:LEAD_CHARS]}"
    found: list[str] = []
    for row in area["entities"]:
        if _named(row["entityName"], whole) or _named(_bare(row["entityName"]), whole):
            found.append(f"names registered entity {row['entityName']}")
    for kind, name, parts in area["counterparties"]:
        if any(_named(part, whole) for part in parts):
            found.append(f"names registered {kind} {name}")
    for name in area["projects"]:
        if _named(name, whole):
            found.append(f"names registered project {name}")
    if people_risk_in_lead(title or "", str(text or ""), terms):
        for country in _countries_in(whole, area["places"]):
            found.append(f"threat to people in {country}, where it has {'; '.join(area['places'][country][:3])}")
        for country in _countries_in(whole, area["programmes"]):
            found.append(f"threat in {country}, home of programme customer {'; '.join(area['programmes'][country][:2])}")
    return list(dict.fromkeys(found))
