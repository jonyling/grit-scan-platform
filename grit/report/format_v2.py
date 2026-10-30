"""Brief format v2 (spec 44): the tables of Wilson's BA-wide report, built by rule from the run. No model is called here;
the escalating-items rows were drafted and checked in the agents step (grit.pipeline.events).

Every table states its basis. A cell with no finding says whether its area was searched this week, so a nil is never
claimed without a search.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Any, Mapping, Sequence

from grit.pipeline.events import DOWN, SAME, UP, event_register, risk_labels

CATEGORIES = ("Operational", "Financial / macro", "Supply / cost", "Competitor / tech", "Regulatory / legal")
OPERATIONAL, FINANCIAL, SUPPLY, COMPETITOR, REGULATORY = CATEGORIES
BA_WIDE = "Business-area wide"
NOT_SEARCHED = "Not searched this week"
NIL = "No item kept (searched)"
NO_FINDING = "No item found in the checked sources."
MAX_PRIORITIES = 12
MAX_CHANGES = 12
_CATEGORY_WORDS = (
    (FINANCIAL, ("financial", "forex", "foreign exchange", "exchange rate", "interest", "inflation", "credit", "funding", "revenue", "margin", "currency", "macro")),
    (SUPPLY, ("supply", "supplier", "vendor", "oem", "single source", "single-source", "cost", "material", "procurement")),
    (REGULATORY, ("regulat", "compliance", "legal", "sanction", "export", "fraud", "brib", "corruption", "data protection", "personal data", "trade")),
    (COMPETITOR, ("competi", "technolog", "emerging", "artificial intelligence", " ai", "digital", "disrupt", "aftermarket")),
)
_COUNTRY_ALIASES = {
    "united states of america": ("United States", "U.S.", "USA", "US"), "united states": ("U.S.", "USA", "US"), "republic of korea": ("South Korea", "Korea"),
    "south korea": ("Korea",), "united arab emirates": ("UAE",), "uae": ("United Arab Emirates",), "abu dhabi": ("UAE",), "dubai": ("UAE",),
    "united kingdom": ("UK", "Britain"), "people's republic of china": ("China",), "hong kong": ("Hong Kong",), "taiwan": ("Taiwan",),
}
_SUFFIX = re.compile(r"\s+(?:[|\u2013\u2014-]\s+[^|\u2013\u2014-]{2,60})$")
_CONFLICT = re.compile(r"(\d[\d,]*)\s+(?:people\s+|persons\s+)?(killed|dead|deaths|died|injured|wounded|missing|cancelled|flights|missiles|drones)\b", re.I)


def clean_title(title: str | None) -> str:
    """A headline without its outlet suffix, such as ' | The Straits Times'."""
    text = " ".join(str(title or "").split())
    for _ in range(2):
        text = _SUFFIX.sub("", text)
    return text.strip()


def category_of_name(name: str) -> str:
    folded = f" {name.casefold()}"
    return next((category for category, words in _CATEGORY_WORDS if any(word in folded for word in words)), OPERATIONAL)


def source_tier(url: str | None, rating: str | None, publisher: str | None) -> str:
    """T1 official or primary; T2 a HIGH-rated outlet; T3 a MEDIUM or LOW-rated outlet; T4 an aggregator or an unrated outlet."""
    from urllib.parse import urlparse

    from grit.core.publisher_family import AGGREGATORS, registrable

    host = (urlparse(str(url or "")).hostname or str(publisher or "")).lower().removeprefix("www.")
    if re.search(r"(?:^|\.)(?:gov|mil|int)(?:\.[a-z]{2})?$|(?:^|\.)gov\.[a-z]{2,3}$|\.gc\.ca$|europa\.eu$|\.go\.[a-z]{2}$", host):
        return "T1"
    if registrable(host) in AGGREGATORS:
        return "T4"
    return {"HIGH": "T2", "MEDIUM": "T3", "LOW": "T3"}.get(str(rating or ""), "T4")


def _lobs(pack: Any) -> list[str]:
    from grit.pipeline.reader_scan import graph_pack

    hierarchy = graph_pack(pack).lob_hierarchy or {}
    return [str(lob["lobName"]) for lob in hierarchy.get("lobs") or [] if lob.get("lobName")]


def _lob_lookup(pack: Any) -> dict[str, str]:
    """Register ids (entity, customer, supplier, competitor row) -> the org chart LOB they belong to."""
    from grit.ontology.lob_hierarchy import resolve_lob
    from grit.pipeline.reader_scan import graph_pack

    business = graph_pack(pack)
    hierarchy = business.lob_hierarchy
    found: dict[str, str] = {}
    exposure = business.exposure or {}
    for key, id_key in (("entities", "entityId"), ("customers", "customerId"), ("suppliers", "supplierId")):
        for row in exposure.get(key) or []:
            for label in str(row.get("lob") or "").split(";"):
                lob = resolve_lob(label.strip(), hierarchy) if label.strip() else None
                if lob:
                    found[str(row[id_key])] = lob["lobName"]
                    break
    for row in (business.competitors or {}).get("competitors") or []:
        if row.get("canonicalLob"):
            found[f"COMPETITOR-row-{row['row']}"] = row["canonicalLob"]
    return found


def lob_of(row: Mapping[str, Any], lookup: Mapping[str, str]) -> str:
    link = row.get("link") or {}
    if link.get("canonical_lob"):
        return str(link["canonical_lob"])
    for item in link.get("involves") or []:
        if isinstance(item, Mapping) and item.get("strong") and lookup.get(str(item.get("node_id") or "")):
            return lookup[str(item["node_id"])]
    for target in link.get("sighting_targets") or []:
        if lookup.get(str(target)):
            return lookup[str(target)]
    return BA_WIDE


def category_of(row: Mapping[str, Any], names: Mapping[str, str]) -> str:
    link = row.get("link") or {}
    if row.get("rung") == "COMPETITOR":
        return COMPETITOR
    if any(isinstance(item, Mapping) and item.get("strong") and str(item.get("cls")) == "Supplier" for item in link.get("involves") or []):
        return SUPPLY
    for item in row.get("themes") or []:
        if isinstance(item, Mapping) and item.get("strong") and item.get("theme_id") in names:
            return category_of_name(names[item["theme_id"]])
    return OPERATIONAL


def _searched(pool: Any, run_id: str, names: Mapping[str, str]) -> dict[str, int]:
    """Queries that returned pages this run, by category. A query that returned nothing is not evidence of a search."""
    lanes = {"COMPETITOR": COMPETITOR, "SUPPLIER": SUPPLY}
    counts: dict[str, int] = defaultdict(int)
    for row in pool.rows("SELECT DISTINCT query_id, lane, target_id FROM sightings WHERE run_id = ?", (run_id,)):
        lane = str(row.get("lane") or "")
        category = lanes.get(lane) or (category_of_name(names[row["target_id"]]) if row.get("target_id") in names else OPERATIONAL)
        counts[category] += 1
    return dict(counts)


def _countries(pack: Any) -> dict[str, list[str]]:
    from grit.pipeline.reader_scan import graph_pack

    found: dict[str, list[str]] = defaultdict(list)
    for row in (graph_pack(pack).exposure or {}).get("entities") or []:
        for country in dict.fromkeys([row.get("countryOfIncorporation"), *(row.get("assetCountries") or [])]):
            if country:
                found[str(country).strip()].append(str(row["entityName"]))
    return dict(found)


def _mentions(country: str, row: Mapping[str, Any]) -> bool:
    from grit.ontology.registry_loader import slugify

    if f"PLACE-{slugify(country)}" in set((row.get("link") or {}).get("place_ids") or []):
        return True
    title = str(row.get("title") or "")
    names = (country, *_COUNTRY_ALIASES.get(country.casefold(), ()))
    return any(re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", title, 0 if name.isupper() else re.IGNORECASE) for name in names)


def build(pool: Any, run: Mapping[str, Any], pack: Any, routed: Sequence[Mapping[str, Any]], conditions: Sequence[Mapping[str, Any]],
          named: Mapping[str, Sequence[Mapping[str, Any]]], registers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The v2 tables. routed: the brief's routed rows; conditions: the brief's condition rows; named: the brief's supplier,
    customer and competitor rows."""
    from grit.ontology.matchers.credibility_lookup import credibility_lookup
    from grit.pipeline.events import risk_names
    from grit.report.lookahead import look_ahead

    run_id, run_date = run["run_id"], date.fromisoformat(run["run_date"])
    names = risk_names(pack)
    shown = [row for row in routed if row["shown"]]
    rising = [row["condition_id"] for row in conditions if row["trend"] == "Escalating"]
    events = event_register(pool, run_id, rows=shown, names=names, escalating=rising)
    drafted = ((pool.run(run_id) or {}).get("config") or {}).get("event_rows") or {}
    short = str((pack.manifest or {}).get("shortLabel") or pack.label)
    event_of = {signal_id: event["event_id"] for event in events for signal_id in event["signal_ids"]}

    def label(event: Mapping[str, Any]) -> str:
        return (drafted.get(event["event_id"]) or {}).get("item") or clean_title(event["lead"].get("title"))

    # 1. Escalating and elevated items.
    elevated = [event for event in events if event["elevated"]][:8]
    escalating_rows = [{
        "event_id": event["event_id"], "band": event["band"], "item": label(event), "risk": event["risk_label"], "tier": event["tier"],
        "row": drafted.get(event["event_id"]), "lead": event["lead"]["signal_id"], "signal_ids": event["signal_ids"][:6], "publishers": event["publishers"],
    } for event in elevated]

    # 2. What changed since the last report.
    previous = {row["condition_id"]: row for row in pool.conditions(pool.latest_run(run["pack"], before=run["run_date"], kind="READER_SCAN")["run_id"])} \
        if pool.latest_run(run["pack"], before=run["run_date"], kind="READER_SCAN") else {}
    changes = []
    for event in [item for item in events if item["band"] in {UP, DOWN} and (item["elevated"] or item["band"] == DOWN)][:MAX_CHANGES]:
        before = event.get("previous_lead")
        changes.append({
            "item": label(event), "risk": event["risk_label"],
            "before": f"{clean_title(before.get('title'))} ({str(before.get('published_at') or '')[:10]})" if before else "Not in the last report.",
            "now": f"{clean_title(event['lead'].get('title'))} ({event['last_date']})" if event["members"] else "No report this week.",
            "direction": f"{UP} New" if event["band"] == UP and event["new"] else f"{UP} Worse" if event["band"] == UP else f"{DOWN} No new report",
            "signal_ids": [event["lead"]["signal_id"]] if event["members"] else [],
        })
    for row in conditions:
        prior = previous.get(row["condition_id"])
        if prior and prior.get("trend_label") and prior["trend_label"] != row["trend"] and "Not established" not in (prior["trend_label"], row["trend"]):
            arrow = UP if row["trend"] == "Escalating" else DOWN if row["trend"] == "Easing" else SAME
            changes.append({"item": row["name"], "risk": risk_labels([row["condition_id"]], names), "before": f"Trend {prior['trend_label']}",
                            "now": f"Trend {row['trend']}", "direction": f"{arrow} {row['trend']}", "signal_ids": []})

    # 3. Look-ahead, from sources linked to the business area only.
    linked = [row for row in shown if row.get("rung") or row.get("condition_ids") or (row.get("route") or {}).get("request_id")]
    look = look_ahead(linked, run_date, event_of=event_of, relevance=lambda row: risk_labels([str(item) for item in row.get("condition_ids") or []], names) or str(row.get("rung") or "").title())

    # 4. Prioritisation.
    priorities = []
    for event in elevated:
        row = drafted.get(event["event_id"]) or {}
        priorities.append({"item": label(event), "trend": f"{event['band']} {'New' if event['new'] else 'Escalating' if event['band'] == UP else 'Elevated'}",
                           "risk": event["risk_label"], "owner": (row.get("response") or {}).get("owner") or "To assign", "signal_ids": [event["lead"]["signal_id"]]})
    covered = {item for event in elevated for item in event["risks"]}
    for row in conditions:
        if row["trend"] == "Escalating" and row["condition_id"] not in covered:
            priorities.append({"item": row["name"], "trend": f"{UP} Escalating", "risk": risk_labels([row["condition_id"]], names), "owner": "To assign", "signal_ids": []})
    for event in [item for item in events if item["band"] == SAME and not item["elevated"] and item["tier"] <= 2 and item["risks"]]:
        if len(priorities) >= MAX_PRIORITIES:
            break
        priorities.append({"item": label(event), "trend": f"{SAME} Continuing", "risk": event["risk_label"], "owner": "To assign", "signal_ids": [event["lead"]["signal_id"]]})

    # 5. Coverage matrix.
    lookup = _lob_lookup(pack)
    lobs = [*_lobs(pack), BA_WIDE]
    searched = _searched(pool, run_id, names)
    cells: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in sorted(shown, key=lambda item: (item.get("tier") or 9, -(item.get("corroboration") or 1))):
        lob = lob_of(row, lookup)
        cells[(lob if lob in lobs else BA_WIDE, category_of(row, names))].append(row)
    coverage = []
    for lob in lobs:
        line = {"lob": lob, "cells": {}}
        for category in CATEGORIES:
            found = cells.get((lob, category)) or []
            if found:
                line["cells"][category] = {"text": "; ".join(clean_title(item.get("title")) for item in found[:2]), "signal_ids": [item["signal_id"] for item in found[:2]],
                                           "more": max(0, len(found) - 2)}
            else:
                line["cells"][category] = {"text": NIL if searched.get(category) else NOT_SEARCHED, "signal_ids": [], "more": 0}
        coverage.append(line)

    # 6. Jurisdictions.
    jurisdictions = []
    up_ids = {signal_id for event in events if event["band"] == UP for signal_id in event["signal_ids"]}
    for country, entities in sorted(_countries(pack).items(), key=lambda pair: (-len(pair[1]), pair[0])):
        found = [row for row in shown if _mentions(country, row)]
        found.sort(key=lambda item: (item.get("tier") or 9, -(item.get("corroboration") or 1)))
        jurisdictions.append({
            "country": country, "entities": entities[:6], "more_entities": max(0, len(entities) - 6),
            "finding": clean_title(found[0].get("title")) if found else NO_FINDING, "signal_ids": [found[0]["signal_id"]] if found else [],
            "trend": UP if any(row["signal_id"] in up_ids for row in found) else SAME if found else "–",
        })

    # 7. Named supplier, customer and partner watch.
    watch = [{"name": item["name"], "type": kind, "text": item["text"], "status": item["status"]}
             for kind, items in named.items() for item in items if item.get("text")]
    targets = {row["target_id"] for row in pool.rows("SELECT DISTINCT target_id FROM sightings WHERE run_id = ? AND lane IN ('SUPPLIER', 'CUSTOMER', 'COMPETITOR', 'ENTITY')", (run_id,))}
    total = sum(len(items) for items in named.values())
    not_searched = sum(1 for items in named.values() for item in items if item.get("item_id") not in targets and not item.get("text"))

    # 8. Quality: the self-challenge gate and conflicts.
    conflicts = []
    for event in events:
        said: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for row in event["members"]:
            for match in _CONFLICT.finditer(f"{row.get('title') or ''} {str(row.get('text') or '')[:1500]}"):
                said[match.group(2).casefold()][match.group(1).replace(",", "")].add(str(row.get("publisher") or "source"))
        for word, values in said.items():
            if len(values) > 1:
                text = "; ".join(f"{', '.join(sorted(outlets)[:2])}: {value} {word}" for value, outlets in sorted(values.items(), key=lambda pair: int(pair[0])))
                conflicts.append({"item": label(event), "said": text, "treatment": "The sources differ. The brief shows each figure with its outlet; check the primary source before quoting one."})
    lob_gaps = [line["lob"] for line in coverage if line["lob"] != BA_WIDE and all(cell["signal_ids"] == [] for cell in line["cells"].values())]
    unsearched = sum(1 for line in coverage for cell in line["cells"].values() if cell["text"] == NOT_SEARCHED)
    competitor_names = [item for item in named.get("Competitor", [])]

    def answer(ok: bool, partly: bool = False) -> str:
        return "Yes" if ok else "Partly" if partly else "No"

    gate = [
        {"question": "Checked macro and financial items (rates, FX, inflation), not just operations?", "answer": answer(bool(searched.get(FINANCIAL))),
         "note": f"{searched.get(FINANCIAL, 0)} financial queries returned pages." if searched.get(FINANCIAL) else "No financial query returned pages this week."},
        {"question": "Swept every line of business?", "answer": answer(not lob_gaps, len(lob_gaps) < len(lobs) - 1),
         "note": f"No item this week for: {', '.join(lob_gaps)}." if lob_gaps else "Every line of business has at least one item."},
        {"question": "Checked competitors?", "answer": answer(bool(searched.get(COMPETITOR))),
         "note": f"{searched.get(COMPETITOR, 0)} competitor queries returned pages; {sum(1 for item in competitor_names if item.get('text'))} named competitors had an item."},
        {"question": "Checked export controls, sanctions and the supply chain?", "answer": answer(bool(searched.get(REGULATORY)) and bool(searched.get(SUPPLY)), bool(searched.get(REGULATORY) or searched.get(SUPPLY))),
         "note": f"Regulatory queries {searched.get(REGULATORY, 0)}; supply and cost queries {searched.get(SUPPLY, 0)}."},
        {"question": "For every nil, actually searched?", "answer": answer(unsearched == 0, True),
         "note": f"{unsearched} coverage cells were not searched this week. They say so and are not claimed as nil." if unsearched else "Every nil cell was searched."},
        {"question": "Checked the named suppliers and customers?", "answer": answer(not_searched == 0, total > not_searched),
         "note": f"{total - not_searched} of {total} named items were searched or reported this week; the rest rotate on the scan plan's cadence."},
        {"question": "Do the sources conflict?", "answer": "Yes" if conflicts else "No", "note": f"{len(conflicts)} conflicts are listed below." if conflicts else "No conflicting figures found within an event."},
    ]

    # Status, arrows and the risk window.
    red = any(event["red"] for event in events)
    status = "RED" if red else "ELEVATED" if elevated or rising else "STABLE"
    arrows = [f"{label(event)} {event['band']}" for event in [*[item for item in events if item["band"] == UP][:4], *[item for item in events if item["band"] == DOWN][:2]]]
    window = [f"{item['date'].day} {item['date'].strftime('%b')}: {item['event'][:90].rsplit(' ', 1)[0]}" for item in look[:2]]

    # Source tiers for every signal the brief may cite.
    tiers = {}
    for row in routed:
        best = None
        for register in registers:
            found = credibility_lookup(str(row.get("url") or ""), register)
            if best is None or found.get("credibilityRating") in {"HIGH", "MEDIUM", "LOW"}:
                best = found
        tiers[row["signal_id"]] = source_tier(row.get("url"), (best or {}).get("credibilityRating"), row.get("publisher"))
    return {
        "status": status, "arrows": arrows, "risk_window": window, "escalating": escalating_rows, "changes": changes,
        "look_ahead": [{**item, "date": item["date"].isoformat()} for item in look], "priorities": priorities, "coverage": coverage, "categories": list(CATEGORIES),
        "jurisdictions": jurisdictions, "watch": watch, "watch_note": {"total": total, "not_searched": not_searched}, "gate": gate, "conflicts": conflicts,
        "tiers": tiers, "short": short,
    }
