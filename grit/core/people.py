"""Where ST Engineering people are, from the internal context R&A supplies (spec 46, section 2).

Shared across adverse, weekly, daily and WSH. Pure apart from reading the staff file and (optionally) the
people-risk lexicon. The file is secrets/internal-context.json. Country-level only: a match names the
country, not a city or site, unless a city is already derivable from the log or a registered address.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

PEOPLE = "PEOPLE"  # adverse-only link rung for a page that matches through the staff log
PEOPLE_RISK = "PEOPLE_RISK"  # lexicon category for threats to people
STALE_DAYS = 30  # older staff data is still used, and the report says it is stale
LEAD_CHARS = 400  # title + lead window for people-risk grounding (same bar as adverse screening)
STAFF_AFFINITY = 2  # within-tier bump when a page grounds to the staff log (not a new rung)

# The hard reader-band override is deliberately narrower than the wider adverse
# people-risk vocabulary. Entries here describe an actual or immediate physical
# threat, rather than a weapons programme, sale, test or assessment.
PHYSICAL_THREAT_TERMS = (
    "armed attack*",
    "armed assault*",
    "armed strike*",
    "military strike*",
    "airstrike*",
    "air strike*",
    "air raid*",
    "missile*",
    "drone*",
    "drone strike*",
    "drone attack*",
    "armed drone*",
    "rocket attack*",
    "rocket fire",
    "shelling",
    "bombing",
    "bomb attack*",
    "physical attack*",
    "physical threat*",
)

_PHYSICAL_THREAT_SPECULATIVE = (
    "may",
    "might",
    "could",
    "would",
    "likely",
    "possibly",
    "possible",
    "potential",
    "proposed",
    "proposes",
    "plans to",
    "planned",
    "considering",
    "expected to",
    "forecast",
    "risk of",
    "threatened to",
)

_PHYSICAL_THREAT_NON_EVENT = (
    "contract",
    "procurement",
    "sale",
    "sales",
    "shipment",
    "export",
    "assessment",
    "scenario",
    "cyber",
    "online",
    "manufacturer",
    "manufacturing",
    "production",
    "technology",
    "capability",
    "programme",
    "program",
    "development",
    "test",
    "testing",
    "trial",
    "exercise",
    "drill",
    "simulation",
    "delivery",
    "shipping",
    "cargo",
    "commercial",
    "survey",
    "inspection",
    "footage",
    "market",
    "shares",
    "stock",
    "order",
    "orders",
    "training",
    "did not happen",
    "no missile",
    "no drone",
    "no attack",
)

# Canonical country and the names news uses for it (the country and its people); then the cities a report may name alone.
COUNTRY_NAMES: dict[str, tuple[str, ...]] = {
    "United Arab Emirates": ("United Arab Emirates", "UAE", "U.A.E.", "Emirati", "Emirates"),
    "Saudi Arabia": ("Saudi Arabia", "Saudi"),
    "Qatar": ("Qatar", "Qatari"),
    "Israel": ("Israel", "Israeli"),
    "Egypt": ("Egypt", "Egyptian"),
    "Kuwait": ("Kuwait", "Kuwaiti"),
    "Bahrain": ("Bahrain", "Bahraini"),
    "Oman": ("Oman", "Omani"),
}
CITIES: dict[str, tuple[str, ...]] = {
    "United Arab Emirates": ("Dubai", "Abu Dhabi", "Sharjah"),
    "Saudi Arabia": ("Riyadh", "Jeddah", "Dammam", "NEOM"),
    "Qatar": ("Doha", "Al Udeid", "Lusail"),
    "Israel": ("Tel Aviv", "Jerusalem", "Haifa", "Holon"),
    "Egypt": ("Cairo", "Alexandria"),
    "Kuwait": ("Kuwait City",),
    "Bahrain": ("Manama",),
    "Oman": ("Muscat",),
}
ALIASES: dict[str, tuple[str, ...]] = {country: (*names, *CITIES.get(country, ())) for country, names in COUNTRY_NAMES.items()}


def long_date(value: date | str | None) -> str:
    if not value or value == "not stated":
        return "not stated"
    day = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    return f"{day.day} {day:%B %Y}"


def canonical_country(name: Any) -> str:
    """'UAE (Dubai)' and 'Dubai' are 'United Arab Emirates'. A name not in ALIASES is returned as written."""
    text = str(name or "").split("(")[0].strip()
    for country, names in ALIASES.items():
        if any(text.casefold() == alias.casefold() for alias in names):
            return country
    return text


def _patterns(country: str) -> list[re.Pattern[str]]:
    names = ALIASES.get(country, (country,))
    # Upper-case abbreviations such as UAE match case-sensitively, so "uae" inside a word or a sentence does not.
    return [re.compile(rf"(?<![\w.]){re.escape(alias)}(?![\w])", 0 if alias.isupper() else re.IGNORECASE) for alias in names]


def _city(country: str, text: Any) -> str | None:
    return next((city for city in CITIES.get(country, ()) if re.search(rf"(?<![\w]){re.escape(city)}(?![\w])", str(text or ""), re.IGNORECASE)), None)


def registered_addresses() -> dict[str, tuple[str, str]]:
    """{entity name: (country, registered address)} from the business areas' entity sheets in secrets/. Empty when none open.
    The addresses stay in secrets/: only the city a scan matches is written to a report."""
    from grit.ontology.pack import manifests
    from grit.ontology.registry_loader import entity_sheet_rows
    from grit.ontology.workbook import workbook_path

    found: dict[str, tuple[str, str]] = {}
    for manifest in manifests().values():
        build = manifest.get("build") or {}
        if not build.get("defaultWorkbook"):
            continue
        try:
            rows = entity_sheet_rows(workbook_path(build["defaultWorkbook"], prefix=str(build.get("prefix") or "URS")), str(build.get("entitySheet") or "2. Entity Address"))
        except (FileNotFoundError, ValueError, OSError):
            continue
        for row in rows:
            name, address = str(row.get("nameofentity") or "").strip(), str(row.get("registeredaddress") or "").strip()
            if name and address:
                found[name] = (canonical_country(row.get("countryofincorporation")), address)
    return found


STAFF_LOG_FILE = "internal-context.json"
STAFF_LOG_APPROVER = "R&A (internal staff log)"


def staff_log_path() -> Path | None:
    """secrets/internal-context.json. The old secrets/adverse/ location is still read until the file is moved."""
    from grit.settings import SECRETS_DIR

    for path in (SECRETS_DIR / STAFF_LOG_FILE, SECRETS_DIR / "adverse" / STAFF_LOG_FILE):
        if path.is_file():
            return path
    return None


def _names(pack: Any) -> set[str]:
    manifest = getattr(pack, "manifest", None) or {}
    aliases = manifest.get("staffLogAliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    return {
        str(name).casefold()
        for name in (getattr(pack, "pack", None), getattr(pack, "label", None), manifest.get("shortLabel"), *aliases)
        if name
    }


@lru_cache(maxsize=1)
def _registers() -> tuple[tuple[str, frozenset[str], tuple[str, ...]], ...]:
    """(pack id, the pack's own names, its registered entity names) for every business-area pack."""
    from grit.ontology.pack import load_ontology_pack, manifests
    from grit.settings import CONFIG_DIR

    found = []
    for name in manifests():
        try:
            pack = load_ontology_pack(CONFIG_DIR, name)
        except (OSError, ValueError, KeyError):
            continue
        if pack.level != "BA":
            continue
        entities = tuple(str(row.get("entityName") or "").casefold() for row in (pack.exposure or {}).get("entities") or [] if row.get("entityName"))
        found.append((name, frozenset(_names(pack)), entities))
    return tuple(found)


def business_areas_of(entity: Any, tag: Any = None) -> set[str]:
    """The business-area packs an entity's staff belong to: the staff log's own business_area tag, else every pack whose
    entity register names the entity ("TransCore" is in Urban Solutions' register). Empty when no register names it."""
    registers = _registers()
    if tag:
        wanted = str(tag).casefold()
        return {name for name, own, _ in registers if wanted in own}
    if not str(entity or "").strip():
        return set()
    word = re.compile(rf"(?<![\w]){re.escape(str(entity or '').casefold())}(?![\w])")
    return {name for name, own, entities in registers if str(entity or "").casefold() in own or any(word.search(item) for item in entities)}


def staff_snapshot(path: Path | None = None, *, pack: Any = None) -> dict[str, Any] | None:
    """The staff log in the exposure-snapshot shape (grit.pipeline.exposure_snapshot): local staff and named entities as
    sites, travellers and assignees as travel. None when there is no staff log or it has no counts.

    With a business-area pack, only that business area's rows are kept (business_areas_of). A row no register or tag
    attributes, such as travellers counted by country only, goes to the Group pack alone. When nothing is left, the
    snapshot says so: "notSupplied" is true and sites and travel are empty."""
    path = path or staff_log_path()
    if path is None or not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    reference = f"Internal staff log ({path.name})"
    group = pack is None or getattr(pack, "level", None) == "GROUP"

    def kept(entity: Any, *tags: Any) -> bool:
        return group or getattr(pack, "pack", None) in business_areas_of(entity, next((tag for tag in tags if tag), None))

    sites: list[dict[str, Any]] = []
    travel: list[dict[str, Any]] = []
    dates: list[str] = []
    sources: list[str] = []
    found = False

    def site(row: Mapping[str, Any], headcount: Any, as_at: str | None, safe: bool, source: Any, block: Mapping[str, Any]) -> None:
        nonlocal found
        country = canonical_country(row.get("country"))
        count = int(headcount or 0)
        if not country or not row.get("entity") or count <= 0:
            return
        found = True
        if not kept(row["entity"], row.get("business_area"), block.get("business_area")):
            return
        sites.append({"entity": str(row["entity"]), "city": _city(country, row.get("country")) or _city(country, row.get("city")) or "Not stated",
                      "country": country, "headcount": count, "reference": str(source or reference),
                      **({"safetyConfirmed": as_at} if safe and as_at else {})})

    local = data.get("middle_east_local_staff") or {}
    local_date = str(local.get("as_at") or data.get("asOf") or "")[:10] or None
    local_safe = "safe" in str(local.get("status") or "").casefold()
    for row in local.get("entries") or []:
        site(row, row.get("headcount"), local_date, local_safe, local.get("source"), local)
    for row in data.get("entity_level_examples") or []:
        site(row, row.get("staff") or row.get("headcount"), None, False, row.get("source"), data)
    for row in data.get("personnel") or []:
        site(row, row.get("headcount"), str(data.get("asOf") or "")[:10] or None, False, row.get("source"), data)
    traveller = data.get("middle_east_travellers_and_assignees") or {}
    travel_date = str(traveller.get("as_at") or "")[:10] or None
    travel_safe = "safe" in str(traveller.get("status") or "").casefold()
    travel_counts: dict[tuple[str, str, str], int] = {}
    by_area = traveller.get("by_business_area") or {}
    if by_area:
        for area, rows in by_area.items():
            if not group and not kept(None, area):
                continue
            records = [
                {"country": country, "destination": country, "travellers": count}
                for country, count in rows.items()
            ] if isinstance(rows, Mapping) else rows
            for row in records or []:
                country = canonical_country(row.get("country"))
                count = int(row.get("travellers") or 0)
                destination = str(row.get("destination") or country).strip()
                if not country or count <= 0:
                    continue
                found = True
                label = f"{area}: {destination}" if group else destination
                key = (str(area), label, country)
                travel_counts[key] = travel_counts.get(key, 0) + count
    else:
        if traveller.get("by_country"):
            found = True
        if kept(None, traveller.get("business_area")):
            for country, count in (traveller.get("by_country") or {}).items():
                canonical = canonical_country(country)
                key = ("", canonical, canonical)
                travel_counts[key] = travel_counts.get(key, 0) + int(count or 0)
    for row in data.get("travel") or []:
        found = True
        if kept(None, row.get("business_area"), data.get("business_area")):
            country = canonical_country(row.get("country"))
            destination = str(row.get("destination") or country).strip()
            label = f"{row['business_area']}: {destination}" if group and row.get("business_area") else destination
            key = (str(row.get("business_area") or ""), label, country)
            travel_counts[key] = travel_counts.get(key, 0) + int(row.get("travellers") or 1)
    for (_area, destination, country), count in travel_counts.items():
        if country and count > 0:
            travel.append({"destination": destination, "country": country, "travellers": count,
                           "reference": str(traveller.get("source") or reference),
                           **({"safetyConfirmed": travel_date} if travel_safe and travel_date else {})})
    for part, when in ((local, local_date), (traveller, travel_date)):
        if part and when:
            dates.append(when)
        if part.get("source"):
            sources.append(str(part["source"]))
    if not found:
        return None
    snapshot = {"pack": str(getattr(pack, "pack", None) or "ste"),
                "asOf": min(dates) if dates else (str(data.get("asOf") or "")[:10] or date.today().isoformat()),
                "preparedBy": "R&A", "sources": [reference, *dict.fromkeys(sources)], "sites": sites, "travel": travel}
    if not group:
        snapshot["businessArea"] = str((getattr(pack, "manifest", None) or {}).get("shortLabel") or getattr(pack, "label", "") or pack.pack)
        snapshot["notSupplied"] = not sites and not travel
    return snapshot


def load_people(path: Path | None = None, *, today: date | None = None, addresses: Mapping[str, tuple[str, str]] | None = None) -> dict[str, Any] | None:
    """The staff by country, or None when R&A supplied no file. Reads the R&A summary layout (middle_east_local_staff,
    middle_east_travellers_and_assignees, entity_level_examples) and the record layout (travel, personnel, staff_safety)."""
    path = path or staff_log_path()
    if path is None or not path.is_file():
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
    # Where in a staffed country: a city the staff log names ("UAE (Dubai)"), else an STE entity's registered address there.
    for row in [*local.get("entries", []), *(data.get("personnel") or [])]:
        country = canonical_country(row.get("country"))
        city = _city(country, row.get("country")) or _city(country, row.get("city"))
        if country in countries and city:
            countries[country].setdefault("sites", {}).setdefault(city, []).append(f"{row.get('entity') or 'staff'}, staff log")
    for name, (country, address) in (registered_addresses() if addresses is None else addresses).items():
        city = _city(country, address)
        if country in countries and city:
            countries[country].setdefault("sites", {}).setdefault(city, []).append(f"{name}, registered address")
    oldest = min((value for value in dates.values() if value), default=None)
    age = (today or date.today()) - date.fromisoformat(oldest) if oldest else None
    return {"countries": countries, "dates": dates, "age_days": age.days if age is not None else None, "stale": age is not None and age.days > STALE_DAYS,
            "caveats": caveats, "gap": data.get("gap"), "local_total": local.get("total_incl_smaller_sites"), "travellers_total": travel.get("total")}


def scope_people(people: Mapping[str, Any] | None, snapshot: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """People-grounding view limited to the exact counts and entities in a pack-filtered staff snapshot."""
    if not people or not snapshot or snapshot.get("notSupplied") is True:
        return None
    countries: dict[str, dict[str, Any]] = {}
    for row in snapshot.get("sites") or []:
        country = canonical_country(row.get("country"))
        record = countries.setdefault(country, {"local_staff": 0, "entities": [], "travellers": 0})
        record["local_staff"] += int(row.get("headcount") or 0)
        entity = str(row.get("entity") or "").strip()
        if entity and entity not in record["entities"]:
            record["entities"].append(entity)
        city = _city(country, row.get("city"))
        if city:
            record.setdefault("sites", {}).setdefault(city, []).append(f"{entity}, staff snapshot" if entity else "staff snapshot")
    for row in snapshot.get("travel") or []:
        country = canonical_country(row.get("country"))
        record = countries.setdefault(country, {"local_staff": 0, "entities": [], "travellers": 0})
        record["travellers"] += int(row.get("travellers") or 0)
    countries = {country: row for country, row in countries.items() if country}
    if not countries:
        return None
    as_of = str(snapshot.get("asOf") or "")
    return {
        **dict(people),
        "countries": countries,
        "dates": {"local_staff": as_of, "travellers": as_of},
        "local_total": sum(row["local_staff"] for row in countries.values()),
        "travellers_total": sum(row["travellers"] for row in countries.values()),
    }


def countries_named(text: str, people: Mapping[str, Any] | None) -> list[str]:
    """The countries with STE people that the text names, by country, people or city."""
    if not people:
        return []
    return [country for country in people["countries"] if any(pattern.search(text or "") for pattern in _patterns(country))]


def node_id(country: str) -> str:
    return "PEOPLE-" + re.sub(r"[^a-z0-9]+", "-", country.casefold()).strip("-")


def presence(country: str, people: Mapping[str, Any]) -> str:
    """'Qatar: 52 local staff (URS), as at 4 November 2026; 21 travellers and assignees, as at 30 October 2026.'"""
    row = people["countries"][country]
    parts = []
    if row["local_staff"] or row["entities"]:
        staff = f"{row['local_staff']} local staff" if row["local_staff"] else "local staff, number not stated"
        entities = f" ({', '.join(row['entities'])})" if row["entities"] else ""
        parts.append(f"{staff}{entities}, as at {long_date(people['dates'].get('local_staff'))}")
    if row["travellers"]:
        parts.append(f"{row['travellers']} travellers and assignees, as at {long_date(people['dates'].get('travellers'))}")
    if row.get("sites"):
        parts.append("sites: " + "; ".join(f"{city} ({'; '.join(where)})" for city, where in row["sites"].items()))
    return f"{country}: " + "; ".join(parts) + "."


def targeted_sites(text: str, countries: Sequence[str], people: Mapping[str, Any] | None) -> list[str]:
    """'Riyadh (ST Engineering Saudi Limited, registered address)' for each STE site city the text names, in the given countries."""
    if not people:
        return []
    found = []
    for country in countries:
        for city, where in (people["countries"].get(country, {}).get("sites") or {}).items():
            if re.search(rf"(?<![\w]){re.escape(city)}(?![\w])", text or "", re.IGNORECASE):
                found.append(f"{city} ({'; '.join(where)})")
    return found


def _term_pattern(term: str) -> re.Pattern[str]:
    stem = term.endswith("*")
    body = r"\s+".join(re.escape(part) for part in term.rstrip("*").split())
    return re.compile(rf"(?<![\w-]){body}{r'[\w-]*' if stem else ''}(?![\w])", re.IGNORECASE)


def people_risk_terms(config_dir: Path | None = None) -> tuple[str, ...]:
    """PEOPLE_RISK terms from Adverse-Lexicon.json. Empty when the file is missing."""
    from grit.settings import CONFIG_DIR

    path = (config_dir or CONFIG_DIR) / "adverse" / "Adverse-Lexicon.json"
    if not path.is_file():
        return ()
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    for category in data.get("categories") or []:
        if category.get("id") == PEOPLE_RISK:
            return tuple(str(term) for term in category.get("terms") or [])
    return ()


def people_risk_in_lead(title: str, text: str, terms: Sequence[str] | None = None) -> list[str]:
    """People-risk lexicon hits in the title or lead. Body-only hits do not count (same bar as adverse Section A)."""
    terms = list(terms) if terms is not None else list(people_risk_terms())
    if not terms:
        return []
    title = str(title or "")
    whole = f"{title}\n{text or ''}"
    title_end = len(title)
    found: list[str] = []
    for term in terms:
        match = _term_pattern(term).search(whole)
        if not match:
            continue
        where = "TITLE" if match.start() < title_end else "LEAD" if match.start() - title_end - 1 < LEAD_CHARS else "BODY"
        if where != "BODY":
            found.append(term)
    return found


def _threat_context_is_guarded(lead: str, match: re.Match[str]) -> bool:
    sentence_start = max(
        (lead.rfind(mark, 0, match.start()) for mark in (". ", "\n", "? ", "! ")),
        default=-1,
    )
    sentence_ends = [
        index
        for index in (lead.find(mark, match.end()) for mark in (". ", "\n", "? ", "! "))
        if index >= 0
    ]
    sentence = lead[sentence_start + 1 : min(sentence_ends) if sentence_ends else len(lead)]
    return any(
        _term_pattern(guard).search(sentence)
        for guard in (*_PHYSICAL_THREAT_SPECULATIVE, *_PHYSICAL_THREAT_NON_EVENT)
    )


def physical_threat_in_lead(title: str, text: str) -> list[str]:
    """Explicit physical-threat hits, excluding speculative and non-event weapons prose."""
    lead = f"{str(title or '')}\n{str(text or '')[:LEAD_CHARS]}"
    found: list[str] = []
    for term in PHYSICAL_THREAT_TERMS:
        match = _term_pattern(term).search(lead)
        if match and not _threat_context_is_guarded(lead, match):
            found.append(term)
    return found


def qualifying_threat_in_lead(
    title: str, text: str, terms: Sequence[str] | None = None
) -> list[str]:
    """People-risk terms plus explicit physical threats safe for a hard override."""
    physical = physical_threat_in_lead(title, text)
    general = people_risk_in_lead(title, text, terms)
    # Bare weapons vocabulary is context-sensitive. It only qualifies through
    # the guarded physical catalog, not a generic lexicon stem match.
    lead = f"{str(title or '')}\n{str(text or '')[:LEAD_CHARS]}"
    qualified: list[str] = []
    for term in general:
        if term.casefold().rstrip("*").startswith(("missile", "drone", "intercepted")):
            continue
        match = _term_pattern(term).search(lead)
        if match and not _threat_context_is_guarded(lead, match):
            qualified.append(term)
    return list(dict.fromkeys([*physical, *qualified]))


@dataclass(frozen=True)
class StaffGrounding:
    """A page that names a staffed country with a people-risk hit in the title or lead. Priority and disclosure only."""

    countries: tuple[str, ...]
    lines: tuple[str, ...]
    terms: tuple[str, ...]

    @property
    def reason(self) -> str:
        joined = "; ".join(self.lines)
        return f"STE people in {', '.join(self.countries)} (internal staff log, R&A; country level): {joined}"

    def as_record(self) -> dict[str, Any]:
        return {"countries": list(self.countries), "lines": list(self.lines), "terms": list(self.terms), "reason": self.reason}


def ground_page(title: str, text: str, people: Mapping[str, Any] | None, *, terms: Sequence[str] | None = None) -> StaffGrounding | None:
    """Staffed-country + people-risk match for weekly/daily/WSH priority. Never invents Direct exposure or a PEOPLE rung."""
    if not people:
        return None
    hits = people_risk_in_lead(title, text, terms)
    if not hits:
        return None
    named = countries_named(f"{title}\n{str(text or '')[:LEAD_CHARS]}", people)
    if not named:
        return None
    lines = tuple(presence(country, people) for country in named)
    return StaffGrounding(tuple(named), lines, tuple(hits))


def apply_staff_priority(priority: Mapping[str, Any] | None, grounding: StaffGrounding | None) -> dict[str, Any]:
    """Elevate within the existing briefing tier: record staff countries and a reason. Does not change the BA ladder."""
    record = dict(priority or {})
    if grounding is None:
        return record
    reasons = list(record.get("reasons") or [])
    if grounding.reason not in reasons:
        reasons.append(grounding.reason)
    record["reasons"] = reasons
    record["staff_countries"] = list(grounding.countries)
    record["staff_priority"] = True
    record["staff_affinity"] = STAFF_AFFINITY
    return record


def append_staff_basis(basis: str | None, grounding: StaffGrounding | None) -> str:
    """Append the staff-log summary to Relevance / Why kept. Never claims Direct exposure from people alone."""
    text = str(basis or "").strip()
    if grounding is None:
        return text
    note = grounding.reason
    if note in text:
        return text
    return f"{text} {note}".strip() if text else note


def staff_priority_key(row: Mapping[str, Any]) -> int:
    """Sort key fragment: 0 when staff_priority is set, else 1 (so staffed threats rise within a tier)."""
    priority = row.get("priority_json") if "priority_json" in row else row.get("priority")
    if isinstance(priority, str):
        try:
            priority = json.loads(priority)
        except json.JSONDecodeError:
            priority = {}
    if isinstance(priority, Mapping) and priority.get("staff_priority"):
        return 0
    return 1


def latest_adverse_included(pool: Any) -> tuple[set[str], set[str]]:
    """Signal ids and source URLs from the latest READY adverse scan's included items. Empty when none."""
    scan = pool.one(
        "SELECT scan_id FROM adverse_scans WHERE status = 'READY' ORDER BY run_date DESC, scan_id DESC LIMIT 1"
    )
    if not scan:
        return set(), set()
    signal_ids: set[str] = set()
    urls: set[str] = set()
    for row in pool.rows(
        "SELECT signal_ids_json, item_json FROM adverse_items WHERE scan_id = ? AND status = 'INCLUDED'",
        (scan["scan_id"],),
    ):
        for signal_id in row.get("signal_ids") or row.get("signal_ids_json") or []:
            if signal_id:
                signal_ids.add(str(signal_id))
        item = row.get("item") or row.get("item_json") or {}
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except json.JSONDecodeError:
                item = {}
        for ref in (item.get("sources") or []) if isinstance(item, Mapping) else []:
            if ref.get("url"):
                urls.add(str(ref["url"]))
        for signal_id in (item.get("signal_ids") or []) if isinstance(item, Mapping) else []:
            if signal_id:
                signal_ids.add(str(signal_id))
    return signal_ids, urls


def is_adverse_signal(
    record: Mapping[str, Any],
    *,
    people: Mapping[str, Any] | None = None,
    adverse_signal_ids: set[str] | None = None,
    adverse_urls: set[str] | None = None,
    terms: Sequence[str] | None = None,
) -> bool:
    """Yes when the URL/signal is in the latest READY adverse included set, or people-risk + staffed country match."""
    signal_id = str(record.get("signal_id") or "")
    url = str(record.get("url") or "")
    if signal_id and signal_id in (adverse_signal_ids or set()):
        return True
    if url and url in (adverse_urls or set()):
        return True
    priority = record.get("priority_json") if "priority_json" in record else record.get("priority")
    if isinstance(priority, str):
        try:
            priority = json.loads(priority)
        except json.JSONDecodeError:
            priority = {}
    if isinstance(priority, Mapping) and priority.get("staff_priority"):
        return True
    return ground_page(str(record.get("title") or ""), str(record.get("text") or ""), people, terms=terms) is not None
