"""A reader pack's weekly query plan. No network, no model, no score.

Every query names the register row it serves: a standing condition or theme, a management request, a named project,
entity, supplier, customer or competitor, a hazard, or a watchpoint. Standing items, requests, projects and hazards run
every week. A name lane can rotate. Its cadence in weeks is set in the scan plan. Each query is due on a fixed week inside
its cadence, derived from its id. The selection for a date is then the same every time it is computed.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any, Iterable, Mapping

from grit.ontology.query_plan import QuerySpec, is_due, reviewed_by_id, search_name

LANES = ("REQUEST", "CONDITION", "THEME", "PROJECT", "SUPPLIER", "CUSTOMER", "HAZARD", "WATCHPOINT", "ENTITY", "COMPETITOR")
NAME_FIELDS = {"SUPPLIER": ("suppliers", "supplierId", "supplierName"), "CUSTOMER": ("customers", "customerId", "customerName"), "ENTITY": ("entities", "entityId", "entityName")}


def _spec(lane: str, query: str, order: int, source: dict[str, str], *, exact: bool = False, cadence: int = 1, lob: str | None = None) -> QuerySpec:
    text = re.sub(r"\s+", " ", query).strip()
    query_id = hashlib.sha256(f"READER|{lane}|{text.casefold()}".encode("utf-8")).hexdigest()[:16]
    return QuerySpec(query_id=query_id, lane=lane, query=text, exact=exact, canonical_lob=lob, priority=None, cadence_weeks=max(1, int(cadence)), order=order, sources=(source,))


def reader_queries(
    scan_plan: Mapping[str, Any],
    watchpoints: Iterable[Mapping[str, Any]] = (),
    requests: Iterable[Mapping[str, Any]] = (),
    *,
    pack: Any | None = None,
    reviewed_aliases: Mapping[str, Any] | None = None,
    run_date: date | None = None,
) -> list[QuerySpec]:
    """Each spec's source names its target register id. With run_date, rotating name lanes keep only the queries due that week."""
    specs: list[QuerySpec] = []
    seen: set[str] = set()
    register = str(scan_plan.get("registerId") or "SCAN-PLAN")

    def add(lane: str, query: str, target_id: str, where: str, **options: Any) -> None:
        spec = _spec(lane, query, len(specs), {"register": register, "where": where, "target": target_id}, **options)
        if spec.query_id not in seen and spec.query:
            seen.add(spec.query_id)
            specs.append(spec)

    for row in requests:
        if row.get("status") != "ACTIVE" or (row.get("pack") and pack is not None and row["pack"] != pack.pack):
            continue
        queries = row.get("queries") or [keyword.replace(" + ", " ") for keyword in (row.get("keywords") or [])[:2]]
        for query in queries:
            add("REQUEST", str(query), str(row["request_id"]), f"management request {row['request_id']}")
    for row in scan_plan.get("conditions") or []:
        for query in row.get("queries") or []:
            add("CONDITION", query, row["conditionId"], f"conditions {row['conditionId']}")
    for row in scan_plan.get("themes") or []:
        for query in row.get("queries") or []:
            add("THEME", query, row["themeId"], f"themes {row['themeId']}")
    for lane, key in (("PROJECT", "projects"), ("SUPPLIER", "suppliers"), ("CUSTOMER", "customers"), ("HAZARD", "hazards")):
        for row in scan_plan.get(key) or []:
            for query in row.get("queries") or []:
                add(lane, query, row["itemId"], f"{key} {row['itemId']}")
    for row in watchpoints:
        watchpoint_id = row.get("watchpoint_id") or row.get("watchpointId")
        raw = row.get("keywords") or []
        subject = raw.get("subject") if isinstance(raw, Mapping) else raw
        keywords = [keyword for keyword in subject or [] if len(keyword) >= 5 or " " in keyword]
        if keywords:
            add("WATCHPOINT", keywords[0] if " " in keywords[0] else f"{keywords[0]} Taiwan", str(watchpoint_id), f"watchpoint {watchpoint_id}")
    named = scan_plan.get("namedItems") or {}
    if named.get("source") == "graph" and pack is not None:
        _name_lanes(add, pack, named.get("lanes") or {}, reviewed_aliases)
    if run_date is not None:
        specs = [spec for spec in specs if spec.cadence_weeks == 1 or is_due(spec, run_date)]
    return specs


def _name_lanes(add: Any, pack: Any, lanes: Mapping[str, Any], reviewed_aliases: Mapping[str, Any] | None) -> None:
    """Entity, supplier, customer and competitor names from the business-area registers, as the names news uses."""
    exposure = pack.exposure
    reviewed = reviewed_by_id(reviewed_aliases)
    countries = sorted({str(row.get("countryOfIncorporation") or "").strip() for row in exposure.get("entities") or []} - {""})
    for lane, (key, id_key, name_key) in NAME_FIELDS.items():
        if lane not in lanes:
            continue
        for row in exposure.get(key) or []:
            if row.get("anonymised") or row.get("counterpartyClass"):
                continue  # 'Thailand NPE Programme Customer' or 'Marine equipment vendors' names no organization that news would print
            query, _basis = search_name(row[name_key], str(row.get(id_key) or ""), reviewed, countries)
            add(lane, query, str(row.get(id_key)), key, exact=True, cadence=lanes[lane].get("cadenceWeeks", 1))
    if "COMPETITOR" in lanes:
        for row in (pack.competitors or {}).get("competitors") or []:
            add("COMPETITOR", row["competitor"], f"COMPETITOR-row-{row['row']}", f"competitors row {row['row']}", exact=True, cadence=lanes["COMPETITOR"].get("cadenceWeeks", 8), lob=row.get("canonicalLob"))


def target_of(spec: QuerySpec) -> str | None:
    return spec.sources[0].get("target") if spec.sources else None
