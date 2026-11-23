"""The weekly exposure snapshot: approved internal facts about where the business is exposed (URS methodology, sections 2
and 4). R&A prepares it each week from internal sources the platform cannot read, such as GRIT and ISOS alerts, the travel
tracker and project papers. A named reviewer or BA head approves it. It records counts, never people's names.

The snapshot becomes approved internal evidence records. These records are the only basis on which an agent may call
exposure confirmed. The report shows the whole snapshot in its exposure section.

    {"pack": "urs", "asOf": "2026-11-02", "preparedBy": "...", "sources": ["..."],
     "sites":     [{"entity", "city", "country", "headcount", "safetyConfirmed", "reference"}],
     "travel":    [{"destination", "country", "travellers", "until", "safetyConfirmed", "reference"}],
     "projects":  [{"name", "entity", "city", "country", "value", "nextMilestone", "reference"}],
     "financial": [{"item", "entity", "amount", "reference"}],
     "alerts":    [{"date", "location", "country", "summary", "nearest": [{"entity", "distanceKm"}], "reference"}]}
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping

SECTIONS = ("sites", "travel", "projects", "financial", "alerts")
EXPOSURE_MAX_AGE_DAYS = 7
REQUIRED = {
    "sites": ("entity", "city", "country", "headcount", "reference"),
    "travel": ("destination", "country", "travellers", "reference"),
    "projects": ("name", "entity", "country", "reference"),
    "financial": ("item", "entity", "amount", "reference"),
    "alerts": ("date", "location", "country", "summary", "reference"),
}
DATES = ("safetyConfirmed", "until", "date")
# A site within this distance of an alert's location is exposed. A site farther away is possibly exposed.
NEAR_KM = 10.0


@dataclass(frozen=True)
class ExposureRecord:
    """One approved exposure fact, normalised without losing its pack boundary."""

    pack: str
    country: str | None
    entity: str
    staff_count: int | None
    staff_scope: str | None
    snapshot_date: date
    last_safety_confirmation: date | None
    status: str
    record_type: str
    reference: str

    @property
    def type(self) -> str:
        """The legacy evidence exposure type."""
        return self.record_type

    @property
    def safety_confirmed(self) -> date | None:
        return self.last_safety_confirmation


@dataclass(frozen=True)
class PrincipalAsk:
    """A named, dated action recommendation for one reader pack."""

    pack: str
    text: str
    owner_profile_id: str
    due_date: date
    red_triggers: tuple[str, ...] = ()


def is_snapshot(document: Any) -> bool:
    return isinstance(document, Mapping) and any(isinstance(document.get(key), list) for key in SECTIONS)


def check_snapshot(document: Mapping[str, Any], *, pack: str | None = None) -> list[str]:
    """Problems that stop the snapshot being admitted. An empty list means it can be admitted."""
    problems: list[str] = []
    document_pack = str(document.get("pack") or "").strip()
    if document_pack and pack and document_pack != str(pack):
        problems.append(f"pack must be '{pack}', not '{document_pack}'.")
    if "pack" in document and not document_pack:
        problems.append("pack must be a non-empty pack id.")
    try:
        date.fromisoformat(str(document.get("asOf") or ""))
    except ValueError:
        problems.append("asOf must be the snapshot date, YYYY-MM-DD.")
    if not any(document.get(key) for key in SECTIONS) and document.get("notSupplied") is not True:
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


def exposure_freshness(document: Mapping[str, Any], *, run_date: str | date) -> tuple[str | None, bool]:
    """Typed approval boundary for snapshot age. Future-dated, missing and old snapshots are stale."""
    raw = document.get("asOf")
    as_of_text = str(raw).strip() if raw is not None else ""
    try:
        as_of = date.fromisoformat(as_of_text)
        if as_of.isoformat() != as_of_text:
            raise ValueError
    except ValueError:
        return as_of_text or None, True
    try:
        run = run_date if isinstance(run_date, date) else date.fromisoformat(str(run_date))
    except ValueError:
        return as_of_text, True
    age = (run - as_of).days
    records = document.get("exposures")
    if records is not None and (not isinstance(records, list) or not records):
        return as_of_text, True
    return as_of_text, document.get("notSupplied") is True or age < 0 or age > EXPOSURE_MAX_AGE_DAYS


def _safety(row: Mapping[str, Any]) -> str:
    return f" Staff safety last confirmed {row['safetyConfirmed']}." if row.get("safetyConfirmed") else " Staff safety confirmation not recorded."


def _record_pack(document: Mapping[str, Any], pack: str | None) -> str:
    chosen = str(pack or document.get("pack") or "").strip()
    if not chosen:
        raise ValueError("The exposure snapshot needs an explicit pack id or a pack on the document.")
    return chosen


def exposure_records(document: Mapping[str, Any], *, pack: str | None = None) -> list[ExposureRecord]:
    """Normalise every approved snapshot row into a typed record with an explicit pack boundary."""
    problems = check_snapshot(document, pack=pack)
    if problems:
        raise ValueError("The exposure snapshot cannot be normalised: " + " ".join(problems))
    scope = _record_pack(document, pack)
    as_of = date.fromisoformat(str(document["asOf"])[:10])
    records: list[ExposureRecord] = []

    def add(row: Mapping[str, Any], *, record_type: str, country: Any, entity: Any,
            count: int | None = None, staff_scope: str | None = None, status: str = "CONFIRMED") -> None:
        last_confirmation = None
        if row.get("safetyConfirmed"):
            last_confirmation = date.fromisoformat(str(row["safetyConfirmed"])[:10])
        records.append(ExposureRecord(
            pack=scope,
            country=str(country).strip() if country is not None and str(country).strip() else None,
            entity=str(entity).strip(),
            staff_count=count,
            staff_scope=staff_scope,
            snapshot_date=as_of,
            last_safety_confirmation=last_confirmation,
            status=status,
            record_type=record_type,
            reference=str(row.get("reference") or ""),
        ))

    for row in document.get("sites") or []:
        add(row, record_type="SITE", country=row["country"], entity=row["entity"],
            count=int(row["headcount"]), staff_scope=f"{scope} local staff")
    for row in document.get("travel") or []:
        add(row, record_type="TRAVEL", country=row["country"], entity=f"Travellers in {row['destination']}",
            count=int(row["travellers"]), staff_scope=f"{scope} travellers and assignees")
    for row in document.get("projects") or []:
        add(row, record_type="CONTRACT", country=row["country"], entity=row["entity"])
    for row in document.get("financial") or []:
        add(row, record_type="OTHER", country=row.get("country"), entity=row["entity"])
    for row in document.get("alerts") or []:
        nearest = sorted(row.get("nearest") or [], key=lambda item: float(item["distanceKm"]))
        closest = nearest[0] if nearest else None
        status = "CONFIRMED" if closest and float(closest["distanceKm"]) <= NEAR_KM else "POSSIBLE"
        add(row, record_type="SITE", country=row["country"],
            entity=closest["entity"] if closest else row["location"], status=status)
    return records


def normalize_exposure_records(document: Mapping[str, Any], *, pack: str | None = None) -> list[ExposureRecord]:
    """Readable alias for callers that normalise a snapshot at an integration boundary."""
    return exposure_records(document, pack=pack)


def _ask_value(raw: Mapping[str, Any], snake: str, camel: str, default: Any = None) -> Any:
    value = raw.get(snake, raw.get(camel, default))
    return value


def _profile_map(profiles: Any) -> Mapping[str, Any]:
    if profiles is None:
        from grit.profiles import load_profiles

        return load_profiles()
    if isinstance(profiles, Mapping):
        return profiles
    return {str(profile.profile_id): profile for profile in profiles}


def principal_ask_problems(raw: Mapping[str, Any], *, pack: str, profiles: Any = None) -> list[str]:
    """Validate a principal ask without depending on report rendering or ReviewGate."""
    problems: list[str] = []
    if not isinstance(raw, Mapping):
        return ["principal ask must be an object."]
    text = str(_ask_value(raw, "text", "text") or "").strip()
    owner_id = str(_ask_value(raw, "owner_profile_id", "ownerProfileId") or "").strip()
    due = _ask_value(raw, "due_date", "dueDate")
    triggers = _ask_value(raw, "red_triggers", "redTriggers", ())
    if not str(pack or "").strip():
        problems.append("principal ask needs a non-empty pack id.")
    if not text:
        problems.append("principal ask needs text.")
    if not owner_id:
        problems.append("principal ask needs owner_profile_id.")
    if due in (None, ""):
        problems.append("principal ask needs an explicit due_date in YYYY-MM-DD format.")
    elif not isinstance(due, (str, date)):
        problems.append("principal ask due_date must be YYYY-MM-DD.")
    else:
        try:
            parsed = due if isinstance(due, date) else date.fromisoformat(due)
            if isinstance(due, str) and parsed.isoformat() != due:
                raise ValueError
        except (TypeError, ValueError):
            problems.append("principal ask due_date must be YYYY-MM-DD.")
    if not isinstance(triggers, (list, tuple)) or not all(isinstance(item, str) and item.strip() for item in triggers):
        problems.append("principal ask red_triggers must be a list of non-empty strings.")
    if owner_id:
        profile = _profile_map(profiles).get(owner_id)
        if profile is None:
            problems.append(f"principal ask owner_profile_id '{owner_id}' is not a known profile.")
        elif callable(getattr(profile, "can_own_principal_ask", None)):
            if not profile.can_own_principal_ask(pack):
                role = getattr(profile, "review_role", None)
                if role not in {"REVIEWER", "BA_HEAD"}:
                    problems.append(f"principal ask owner_profile_id '{owner_id}' must be a REVIEWER or BA_HEAD.")
                else:
                    problems.append(f"principal ask owner_profile_id '{owner_id}' is not scoped to pack '{pack}'.")
        elif getattr(profile, "review_role", None) not in {"REVIEWER", "BA_HEAD"}:
            problems.append(f"principal ask owner_profile_id '{owner_id}' must be a REVIEWER or BA_HEAD.")
        elif pack not in tuple(getattr(profile, "packs", ()) or ()):
            problems.append(f"principal ask owner_profile_id '{owner_id}' is not scoped to pack '{pack}'.")
    return problems


def check_principal_ask(raw: Mapping[str, Any], *, pack: str, profiles: Any = None) -> list[str]:
    return principal_ask_problems(raw, pack=pack, profiles=profiles)


def principal_ask(raw: Mapping[str, Any], *, pack: str, profiles: Any = None) -> PrincipalAsk:
    """Validate and return the typed principal ask contract."""
    problems = principal_ask_problems(raw, pack=pack, profiles=profiles)
    if problems:
        raise ValueError("The principal ask cannot be admitted: " + " ".join(problems))
    due = _ask_value(raw, "due_date", "dueDate")
    triggers = _ask_value(raw, "red_triggers", "redTriggers", ())
    return PrincipalAsk(
        pack=pack,
        text=str(_ask_value(raw, "text", "text")).strip(),
        owner_profile_id=str(_ask_value(raw, "owner_profile_id", "ownerProfileId")).strip(),
        due_date=due if isinstance(due, date) else date.fromisoformat(due),
        red_triggers=tuple(item.strip() for item in triggers),
    )


def normalize_principal_ask(raw: Mapping[str, Any], *, pack: str, profiles: Any = None) -> PrincipalAsk:
    return principal_ask(raw, pack=pack, profiles=profiles)


def snapshot_records(document: Mapping[str, Any], *, pack: str | None = None) -> list[dict[str, Any]]:
    """Approved internal evidence records, one per snapshot entry, in the format add_internal_evidence admits."""
    problems = check_snapshot(document, pack=pack)
    if problems:
        raise ValueError("The exposure snapshot cannot be admitted: " + " ".join(problems))
    as_of = str(document["asOf"])
    records: list[dict[str, Any]] = []

    def add(title: str, text: str, reference: str, kind: str, status: str, entity: str) -> None:
        records.append({"title": title, "text": f"Approved internal record, as of {as_of}: {text}", "reference": reference,
                        "published_at": as_of, "exposure": {"type": kind, "status": status, "entity": entity}})

    for row in document.get("sites") or []:
        known = str(row.get("city") or "").strip() not in {"", "Not stated"}
        place, where = (row["city"], f"at its site in {row['city']}, {row['country']}") if known else (row["country"], f"in {row['country']}")
        add(f"{row['entity']}: {row['headcount']} staff in {place}",
            f"{row['entity']} has {row['headcount']} staff {where}." + _safety(row),
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
    extra = {key: document[key] for key in ("pack", "businessArea", "notSupplied", "principalAsk", "principal_ask") if key in document}
    return {"asOf": document.get("asOf"), "preparedBy": document.get("preparedBy"), "sources": list(document.get("sources") or []), **extra, **keep}


def merge_summaries(first: Mapping[str, Any] | None, second: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """One report snapshot from two summaries, such as the staff log and a reviewer-approved snapshot. Rows are joined,
    the approvers and sources are listed together, and asOf is the later date."""
    parts = [part for part in (first, second) if part]
    # A staff log with none of this business area's staff adds nothing to a reviewer's snapshot.
    parts = [part for part in parts if not part.get("notSupplied")] or parts[:1]
    if len(parts) < 2:
        return dict(parts[0]) if parts else None
    a, b = parts
    if a.get("pack") and b.get("pack") and a["pack"] != b["pack"]:
        raise ValueError(f"Cannot merge exposure snapshots for packs '{a['pack']}' and '{b['pack']}'.")
    merged: dict[str, Any] = {
        "asOf": max(str(a.get("asOf") or ""), str(b.get("asOf") or "")) or None,
        "preparedBy": "; ".join(dict.fromkeys(str(part["preparedBy"]) for part in parts if part.get("preparedBy"))) or None,
        "approvedBy": "; ".join(dict.fromkeys(str(part["approvedBy"]) for part in parts if part.get("approvedBy"))) or None,
        "sources": list(dict.fromkeys([*(a.get("sources") or []), *(b.get("sources") or [])])),
    }
    merged_pack = a.get("pack") or b.get("pack")
    if merged_pack:
        merged["pack"] = merged_pack
    for key in ("principalAsk", "principal_ask"):
        value = b.get(key) or a.get(key)
        if value:
            merged[key] = value
            break
    for key in SECTIONS:
        rows = [*(a.get(key) or []), *(row for row in b.get(key) or [] if row not in (a.get(key) or []))]
        if rows:
            merged[key] = rows
    return merged
