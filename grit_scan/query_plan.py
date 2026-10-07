"""Query plan: turn the ontology into search queries. No network, no scoring, no model.

Every query keeps its source, so a reviewer can trace it to a workbook row. Each lane has a cadence
in weeks, and each query is due on a fixed weekday slot inside its cadence. A weekly run takes the
due queries, lane by lane, up to a cap. The whole ontology is covered once every 12 weeks, and the
selection for a date is the same every time it is computed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from grit_scan.lob_hierarchy import resolve_lob
from grit_scan.ontology import OntologyPack, load_ontology_pack
from grit_scan.ontology_graph import short_names, usable_name

# Lane order is the order of preference when a cap trims the due queries.
LANES = (
    "TAG_CLUSTER",
    "EMERGING_THEME",
    "SCAN_KEYWORD",
    "TIER1_TERM",
    "LOB_KEYWORD",
    "STE_THEME",
    "MANAGEMENT_FOCUS",
    "SUPPLIER",
    "CUSTOMER",
    "COMPETITOR",
    "ENTITY",
    "CLUSTER_TERM",
)
EXACT_LANES = frozenset({"SUPPLIER", "CUSTOMER", "COMPETITOR", "ENTITY"})
# The free Brave plan allows about 2,000 queries a month. Five runs of 350 leave room for retries.
DEFAULT_MAX_QUERIES = 350
MAX_QUERY_CHARS = 400
_RANK = {"Critical": 0, "Highest Priority": 0, "KEY": 0, "High": 1, "TIER_2": 1, "Medium": 2, "Emerging": 2, "EMERGING": 2}
_NO_PRIORITY_RANK = 3
_CADENCE: dict[str, int | dict[str | None, int]] = {
    "TAG_CLUSTER": {"Critical": 1, "High": 2, "Medium": 4, None: 4},
    "EMERGING_THEME": 4,
    "SCAN_KEYWORD": 2,
    "TIER1_TERM": 4,
    "LOB_KEYWORD": 4,
    "STE_THEME": {"KEY": 2, "TIER_2": 4, "EMERGING": 4, None: 4},
    "MANAGEMENT_FOCUS": 1,
    "SUPPLIER": 1,
    "CUSTOMER": 1,
    "COMPETITOR": 8,
    "ENTITY": 2,
    "CLUSTER_TERM": 12,
}


@dataclass(frozen=True)
class QuerySpec:
    query_id: str
    lane: str
    query: str
    exact: bool
    canonical_lob: str | None
    priority: str | None
    cadence_weeks: int
    order: int
    sources: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def as_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["sources"] = list(self.sources)
        return record


def _normal(query: str) -> str:
    return re.sub(r"\s+", " ", query).strip(" .;,").casefold()


def _cadence(lane: str, priority: str | None) -> int:
    value = _CADENCE[lane]
    return value if isinstance(value, int) else value.get(priority, value[None])


def _rank(priority: str | None) -> int:
    return _RANK.get(priority or "", _NO_PRIORITY_RANK)


def search_name(name: str, node_id: str, reviewed: Mapping[str, Sequence[str]], countries: Sequence[str]) -> tuple[str, str]:
    """The name a news item uses for a registered name, and where it came from. A legal name such as 'BYD (SINGAPORE) PTE LTD' finds nothing as an exact phrase."""
    for alias in reviewed.get(node_id, ()):
        if usable_name(alias):
            return alias, "REVIEWED"
    short = short_names(name, countries)
    return (short[0], "GENERATED") if short else (name, "REGISTER")


def _items(pack: OntologyPack, reviewed: Mapping[str, Sequence[str]] | None = None) -> Iterator[tuple[str, str, str | None, str | None, dict[str, str]]]:
    """Yield (lane, query, canonical LOB, priority, source) in lane order."""
    libraries = (pack.keyword_library or {}).get("libraries") or []
    register = "URS-KEYWORD-LIBRARY"
    for library in libraries:
        for tag in library["tagClusters"]:
            yield "TAG_CLUSTER", tag["name"], library["canonicalLob"], tag["priority"], {"register": register, "where": f"{library['sheet']} tagClusters"}
    for library in libraries:
        for theme in library["emergingThemes"]:
            yield "EMERGING_THEME", theme, library["canonicalLob"], None, {"register": register, "where": f"{library['sheet']} emergingThemes"}
    for library in libraries:
        for term in (library["scanKeywords"] or {}).get("terms", []):
            yield "SCAN_KEYWORD", term, library["canonicalLob"], None, {"register": register, "where": f"{library['sheet']} scanKeywords"}
    for library in libraries:
        for group in library["tier1"]:
            for term in group["terms"]:
                yield "TIER1_TERM", term, library["canonicalLob"], "Highest Priority", {"register": register, "where": f"{library['sheet']} tier1"}
    for lob in pack.lob_profiles.get("lobs") or []:
        for keyword in lob["keywords"]:
            yield "LOB_KEYWORD", keyword, lob.get("canonicalLob"), None, {"register": "URS-LOB-PROFILES", "where": f"{lob['lobName']} keywords"}
    for theme in pack.themes.get("themes") or []:
        for keyword in theme["keywords"]:
            yield "STE_THEME", keyword, None, theme["tier"], {"register": "URS-STE-THEMES", "where": theme["themeName"]}

    management = pack.management_focus or {}
    for item in management.get("focusItems") or []:
        yield "MANAGEMENT_FOCUS", item["focus"], item.get("canonicalLob"), None, {"register": "URS-MANAGEMENT-FOCUS", "where": f"focusItems {item['scope']}"}
    for request in management.get("requests") or []:
        if not request["countries"]:
            yield "MANAGEMENT_FOCUS", request["text"], None, None, {"register": "URS-MANAGEMENT-FOCUS", "where": f"request {request['number']}"}
            continue
        # "Rail, Road & Airport markets for the following countries" names the sectors. No sector is added.
        match = re.match(r"^(.*?)\s+markets\b", request["scope"])
        sectors = [part.strip() for part in re.split(r",|&|\band\b", match.group(1)) if part.strip()] if match else []
        for country in request["countries"]:
            for sector in sectors:
                yield "MANAGEMENT_FOCUS", f"{country} {sector} sector", None, None, {"register": "URS-MANAGEMENT-FOCUS", "where": f"request {request['number']} {country}"}

    exposure = pack.exposure
    reviewed = reviewed or {}
    countries = sorted({str(row.get("countryOfIncorporation") or "").strip() for row in exposure.get("entities") or []} - {""})

    def named(row: Mapping[str, Any], id_field: str, name_field: str, where: str) -> tuple[str, dict[str, str]]:
        query, basis = search_name(row[name_field], str(row.get(id_field) or ""), reviewed, countries)
        return query, {"register": "URS-EXPOSURE", "where": where, "name": row[name_field], "searchNameBasis": basis}

    for supplier in exposure.get("suppliers") or []:
        query, source = named(supplier, "supplierId", "supplierName", "suppliers")
        yield "SUPPLIER", query, None, None, source
    for customer in exposure.get("customers") or []:
        query, source = named(customer, "customerId", "customerName", "customers")
        yield "CUSTOMER", query, None, None, source
    for row in (pack.competitors or {}).get("competitors") or []:
        yield "COMPETITOR", row["competitor"], row.get("canonicalLob"), None, {"register": "URS-COMPETITORS", "where": f"{row['lobLabel']} row {row['row']}"}
    for entity in exposure.get("entities") or []:
        lob = resolve_lob(entity.get("lob") or "", pack.lob_hierarchy)
        query, source = named(entity, "entityId", "entityName", "entities")
        yield "ENTITY", query, lob["lobName"] if lob else None, None, source
    for library in libraries:
        for cluster in library["clusters"]:
            for group in cluster["groups"]:
                for term in group["terms"]:
                    yield "CLUSTER_TERM", term, library["canonicalLob"], cluster["priority"], {"register": register, "where": f"{library['sheet']} cluster {cluster['number']}"}


def reviewed_by_id(reviewed_aliases: Mapping[str, Any] | None) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for item in (reviewed_aliases or {}).get("aliases") or []:
        found.setdefault(str(item["entity_id"]), []).append(str(item["alias"]))
    return found


def build_query_plan(pack: OntologyPack, reviewed_aliases: Mapping[str, Any] | None = None) -> list[QuerySpec]:
    """Build every query the ontology supports. A repeated query keeps its first lane and merges the sources."""
    merged: dict[str, dict[str, Any]] = {}
    for lane, query, lob, priority, source in _items(pack, reviewed_by_id(reviewed_aliases)):
        text = re.sub(r"\s+", " ", query).strip()
        if not text:
            continue
        if len(text) > MAX_QUERY_CHARS:
            raise ValueError(f"Query is longer than {MAX_QUERY_CHARS} characters: {text[:60]}")
        key = _normal(text)
        entry = merged.get(key)
        if entry is None:
            merged[key] = {"lane": lane, "query": text, "lob": lob, "priority": priority, "cadence": _cadence(lane, priority), "sources": [source]}
            continue
        entry["sources"].append(source)
        entry["cadence"] = min(entry["cadence"], _cadence(lane, priority))
        entry["lob"] = entry["lob"] or lob
        if _rank(priority) < _rank(entry["priority"]):
            entry["priority"] = priority
    plan = []
    for order, entry in enumerate(merged.values()):
        lane = entry["lane"]
        query_id = hashlib.sha256(f"{lane}|{_normal(entry['query'])}".encode("utf-8")).hexdigest()[:16]
        plan.append(
            QuerySpec(
                query_id=query_id,
                lane=lane,
                query=entry["query"],
                exact=lane in EXACT_LANES,
                canonical_lob=entry["lob"],
                priority=entry["priority"],
                cadence_weeks=entry["cadence"],
                order=order,
                sources=tuple(entry["sources"]),
            )
        )
    return plan


def week_index(run_date: date) -> int:
    return run_date.toordinal() // 7


def is_due(spec: QuerySpec, run_date: date) -> bool:
    return week_index(run_date) % spec.cadence_weeks == int(spec.query_id[:8], 16) % spec.cadence_weeks


def select_queries(plan: list[QuerySpec], run_date: date, max_queries: int = DEFAULT_MAX_QUERIES) -> dict[str, Any]:
    """Pick this run's queries. The record is enough to reconstruct the choice and to see what the cap deferred."""
    due = [spec for spec in plan if is_due(spec, run_date)]
    due.sort(key=lambda spec: (LANES.index(spec.lane), _rank(spec.priority), spec.order))
    selected = due[:max_queries]
    return {
        "runDate": run_date.isoformat(),
        "weekIndex": week_index(run_date),
        "maxQueries": max_queries,
        "planSize": len(plan),
        "due": len(due),
        "selected": [spec.as_record() for spec in selected],
        "deferredQueryIds": [spec.query_id for spec in due[max_queries:]],
    }


def balanced_sample(selected: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """Take count queries by dealing one per lane in turn, so a small run touches every lane. Each lane keeps its order."""
    queues = [[item for item in selected if item["lane"] == lane] for lane in LANES]
    chosen: list[dict[str, Any]] = []
    while len(chosen) < count and any(queues):
        for queue in queues:
            if queue and len(chosen) < count:
                chosen.append(queue.pop(0))
    return chosen


def _main() -> None:
    parser = argparse.ArgumentParser(description="Print this week's query plan. This makes no network call.")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today(), help="Run date, YYYY-MM-DD.")
    parser.add_argument("--max-queries", type=int, default=DEFAULT_MAX_QUERIES)
    parser.add_argument("--list", type=int, default=10, help="How many selected queries to print.")
    parser.add_argument("--json", type=Path, help="Write the full selection record to this path.")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    plan = build_query_plan(load_ontology_pack(root / "config", "urs"), json.loads((root / "config" / "registry_aliases.json").read_text(encoding="utf-8-sig")))
    selection = select_queries(plan, args.date, args.max_queries)
    print(f"Plan: {len(plan)} queries. Run date {selection['runDate']}, week {selection['weekIndex']}.")
    print(f"Due: {selection['due']}. Selected: {len(selection['selected'])}. Deferred by the cap: {len(selection['deferredQueryIds'])}.")
    print(f"{'lane':18} {'plan':>5} {'selected':>8}")
    for lane in LANES:
        print(f"{lane:18} {sum(spec.lane == lane for spec in plan):>5} {sum(item['lane'] == lane for item in selection['selected']):>8}")
    for item in selection["selected"][: args.list]:
        print(f"  {item['lane']:16} {item['priority'] or '-':>8}  {item['query']}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(selection, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    _main()
