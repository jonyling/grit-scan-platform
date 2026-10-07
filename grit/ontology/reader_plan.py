"""The reader pack's weekly query plan. No network, no model, no score.

Every query names the register row it serves: a standing condition, a project, a supplier, a customer, a hazard, or a
watchpoint. Every query runs every week. The general query plan (grit.ontology.query_plan) covers the business-area
ontology on a 12-week cycle. This plan covers what one reader must hear about every week.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping

from grit.ontology.query_plan import QuerySpec

LANES = ("REQUEST", "CONDITION", "PROJECT", "SUPPLIER", "CUSTOMER", "HAZARD", "WATCHPOINT")


def _spec(lane: str, query: str, order: int, source: dict[str, str]) -> QuerySpec:
    text = re.sub(r"\s+", " ", query).strip()
    query_id = hashlib.sha256(f"READER|{lane}|{text.casefold()}".encode("utf-8")).hexdigest()[:16]
    return QuerySpec(query_id=query_id, lane=lane, query=text, exact=False, canonical_lob=None, priority=None, cadence_weeks=1, order=order, sources=(source,))


def reader_queries(scan_plan: Mapping[str, Any], watchpoints: Iterable[Mapping[str, Any]] = (), requests: Iterable[Mapping[str, Any]] = ()) -> list[QuerySpec]:
    """Each spec's source names its target register id, so a search result can be traced to the row it was written for."""
    specs: list[QuerySpec] = []
    seen: set[str] = set()

    def add(lane: str, query: str, target_id: str, where: str) -> None:
        spec = _spec(lane, query, len(specs), {"register": "URS-TW-SCAN-PLAN", "where": where, "target": target_id})
        if spec.query_id not in seen and spec.query:
            seen.add(spec.query_id)
            specs.append(spec)

    for row in requests:
        if row.get("status") != "ACTIVE":
            continue
        for keyword in (row.get("keywords") or [])[:2]:
            add("REQUEST", str(keyword), str(row["request_id"]), f"management request {row['request_id']}")
    for row in scan_plan.get("conditions") or []:
        for query in row.get("queries") or []:
            add("CONDITION", query, row["conditionId"], f"conditions {row['conditionId']}")
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
    return specs


def target_of(spec: QuerySpec) -> str | None:
    return spec.sources[0].get("target") if spec.sources else None
