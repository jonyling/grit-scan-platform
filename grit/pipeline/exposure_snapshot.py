"""The weekly exposure snapshot: approved internal facts about where the business is exposed (URS methodology, sections 2
and 4). R&A prepares it each week from internal sources the platform cannot read, such as GRIT and ISOS alerts, the travel
tracker and project papers, and a named reviewer or BA head approves it. It records counts, never people's names.

The snapshot becomes approved internal evidence records, the only basis on which an agent may call exposure confirmed, and
it is shown whole in the report's exposure section.

    {"pack": "urs", "asOf": "2026-11-02", "preparedBy": "...", "sources": ["..."],
     "sites":     [{"entity", "city", "country", "headcount", "safetyConfirmed", "reference"}],
     "travel":    [{"destination", "country", "travellers", "until", "safetyConfirmed", "reference"}],
     "projects":  [{"name", "entity", "city", "country", "value", "nextMilestone", "reference"}],
     "financial": [{"item", "entity", "amount", "reference"}],
     "alerts":    [{"date", "location", "country", "summary", "nearest": [{"entity", "distanceKm"}], "reference"}]}
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

SECTIONS = ("sites", "travel", "projects", "financial", "alerts")
REQUIRED = {
    "sites": ("entity", "city", "country", "headcount", "reference"),
    "travel": ("destination", "country", "travellers", "reference"),
    "projects": ("name", "entity", "country", "reference"),
    "financial": ("item", "entity", "amount", "reference"),
    "alerts": ("date", "location", "country", "summary", "reference"),
}
DATES = ("safetyConfirmed", "until", "date")
# A site within this distance of an alert's location is exposed; further away, possibly exposed.
NEAR_KM = 10.0


def is_snapshot(document: Any) -> bool:
    return isinstance(document, Mapping) and any(isinstance(document.get(key), list) for key in SECTIONS)


def check_snapshot(document: Mapping[str, Any]) -> list[str]:
    """Problems that stop the snapshot being admitted. An empty list means it can be admitted."""
    problems: list[str] = []
    try:
        date.fromisoformat(str(document.get("asOf") or ""))
    except ValueError:
        problems.append("asOf must be the snapshot date, YYYY-MM-DD.")
    if not any(document.get(key) for key in SECTIONS):
        problems.append(f"A snapshot needs at least one of: {', '.join(SECTIONS)}.")
    for section in SECTIONS:
        rows = document.get(section) or []
        if not isinstance(rows, list):
            problems.append(f"{section} must be a list.")
            continue
        for number, row in enumerate(rows, 1):
            where = f"{section} {number}"
            if not isinstance(row, Mapping):
                problems.append(f"{where}: each entry must be an object.")
                continue
            missing = [key for key in REQUIRED[section] if row.get(key) in (None, "")]
            if missing:
                problems.append(f"{where}: needs {', '.join(missing)}.")
            for key in DATES:
                if row.get(key):
                    try:
                        date.fromisoformat(str(row[key])[:10])
                    except ValueError:
                        problems.append(f"{where}: {key} must be YYYY-MM-DD.")
            for key in ("headcount", "travellers"):
                if key in row and (not isinstance(row[key], int) or isinstance(row[key], bool) or row[key] < 0):
                    problems.append(f"{where}: {key} must be a whole number.")
            for near in row.get("nearest") or []:
                if not isinstance(near, Mapping) or not near.get("entity") or not isinstance(near.get("distanceKm"), (int, float)):
                    problems.append(f"{where}: each nearest entry needs an entity and distanceKm.")
    return problems


def _safety(row: Mapping[str, Any]) -> str:
    return f" Staff safety last confirmed {row['safetyConfirmed']}." if row.get("safetyConfirmed") else " Staff safety confirmation not recorded."


def snapshot_records(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Approved internal evidence records, one per snapshot entry, in the format add_internal_evidence admits."""
    problems = check_snapshot(document)
    if problems:
        raise ValueError("The exposure snapshot cannot be admitted: " + " ".join(problems))
    as_of = str(document["asOf"])
    records: list[dict[str, Any]] = []

    def add(title: str, text: str, reference: str, kind: str, status: str, entity: str) -> None:
        records.append({"title": title, "text": f"Approved internal record, as of {as_of}: {text}", "reference": reference,
                        "published_at": as_of, "exposure": {"type": kind, "status": status, "entity": entity}})

    for row in document.get("sites") or []:
        add(f"{row['entity']}: {row['headcount']} staff in {row['city']}",
            f"{row['entity']} has {row['headcount']} staff at its site in {row['city']}, {row['country']}." + _safety(row),
            str(row["reference"]), "SITE", "CONFIRMED", str(row["entity"]))
    for row in document.get("travel") or []:
        until = f" until {row['until']}" if row.get("until") else ""
        add(f"{row['travellers']} travellers in {row['destination']}",
            f"{row['travellers']} business travellers or assignees are in {row['destination']}, {row['country']}{until}." + _safety(row),
            str(row["reference"]), "TRAVEL", "CONFIRMED", f"Travellers in {row['destination']}")
    for row in document.get("projects") or []:
        place = ", ".join(str(part) for part in (row.get("city"), row["country"]) if part)
        details = "".join(f" {label}: {row[key]}." for key, label in (("value", "Value"), ("nextMilestone", "Next milestone")) if row.get(key))
        add(f"{row['name']} ({row['entity']})", f"{row['entity']} delivers {row['name']} in {place}.{details}",
            str(row["reference"]), "CONTRACT", "CONFIRMED", str(row["entity"]))
    for row in document.get("financial") or []:
        add(f"{row['item']}: {row['amount']}", f"{row['item']} for {row['entity']}: {row['amount']}.",
            str(row["reference"]), "OTHER", "CONFIRMED", str(row["entity"]))
    for row in document.get("alerts") or []:
        nearest = sorted(row.get("nearest") or [], key=lambda item: float(item["distanceKm"]))
        distances = "; ".join(f"{item['entity']} {float(item['distanceKm']):g} km away" for item in nearest)
        closest = nearest[0] if nearest else None
        status = "CONFIRMED" if closest and float(closest["distanceKm"]) <= NEAR_KM else "POSSIBLE"
        add(f"Internal alert {row['date']}: {row['location']}",
            f"Alert dated {row['date']} for {row['location']}, {row['country']}: {row['summary']}" + (f" Nearest sites: {distances}." if distances else ""),
            str(row["reference"]), "SITE", status, str(closest["entity"]) if closest else str(row["location"]))
    return records


def snapshot_summary(document: Mapping[str, Any]) -> dict[str, Any]:
    """What the agents and the report show of the snapshot: every section, without the references."""
    keep = {key: [{field: value for field, value in row.items() if field != "reference"} for row in document.get(key) or []]
            for key in SECTIONS if document.get(key)}
    return {"asOf": document.get("asOf"), "preparedBy": document.get("preparedBy"), "sources": list(document.get("sources") or []), **keep}
