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
NIL_WIDE = "No item kept (business-area-wide search only)"
NO_FINDING = "No item found in the checked sources."
NO_COUNTRY_SEARCH = "No item found; no country-specific search ran this week."
MAX_PRIORITIES = 12
MAX_CHANGES = 12
_CATEGORY_WORDS = (
    (FINANCIAL, ("financial", "economic slowdown", "recession", "forex", "foreign exchange", "exchange rate", "interest", "inflation", "credit", "funding", "revenue", "margin", "currency", "macro")),
    (SUPPLY, ("supply", "supplier", "vendor", "oem", "single source", "single-source", "cost", "material", "procurement")),
    (REGULATORY, ("regulat", "compliance", "legal", "sanction", "export", "fraud", "brib", "corruption", "data protection", "personal data", "trade")),
    (COMPETITOR, ("competi", "technolog", "emerging", "artificial intelligence", " ai", "digital", "disrupt", "aftermarket")),
)
_COUNTRY_ALIASES = {
    "united states of america": ("United States", "U.S.", "USA", "US"), "united states": ("U.S.", "USA", "US"), "republic of korea": ("South Korea", "Korea"),
    "south korea": ("Korea",), "united arab emirates": ("UAE",), "uae": ("United Arab Emirates",), "abu dhabi": ("UAE",), "dubai": ("UAE",),
    "united kingdom": ("UK", "Britain"), "people's republic of china": ("China",), "hong kong": ("Hong Kong",), "taiwan": ("Taiwan",),
}
# An outlet or breadcrumb suffix: " | The Straits Times", " - Reuters", " < IT·Gaming < 기사본문".
_SUFFIX = re.compile(r"\s+(?:[|\u2013\u2014<-]\s+[^|\u2013\u2014<-]{2,60})$")
_CONFLICT = re.compile(r"(\d[\d,]*)\s+(?:people\s+|persons\s+)?(killed|dead|deaths|died|injured|wounded|missing|cancelled|flights|missiles|drones)\b", re.I)


def clean_title(title: str | None) -> str:
    """A headline without its outlet or breadcrumb suffix, such as ' | The Straits Times'."""
    text = " ".join(str(title or "").split())
    for _ in range(2):
        text = _SUFFIX.sub("", text)
    return text.strip()


def category_of_name(name: str) -> str:
    folded = f" {name.casefold()}"
    return next((category for category, words in _CATEGORY_WORDS if any(word in folded for word in words)), OPERATIONAL)


def short_text(text: str, limit: int = 120) -> str:
    """The first sentence when it fits, else the text cut at a word boundary and marked with an ellipsis."""
    text = " ".join(str(text or "").split())
    sentence = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text, maxsplit=1)[0]
    if len(sentence) <= limit:
        return sentence
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"


def source_tier(url: str | None, rating: str | None, publisher: str | None, source_class: str | None = None) -> str:
    """The source band by rule (grit.core.source_bands): T1, T2, T3, UNRATED, T4 or CONTESTED."""
    from grit.core.source_bands import source_band

    return source_band(url, rating, publisher, source_class=source_class)


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


def _query_category(row: Mapping[str, Any], names: Mapping[str, str]) -> str:
    lane = str(row.get("lane") or "")
    if lane == "COMPETITOR":
        return COMPETITOR
    if lane == "SUPPLIER":
        return SUPPLY
    return category_of_name(names[row["target_id"]]) if row.get("target_id") in names else OPERATIONAL


def _searched(pool: Any, run_id: str, names: Mapping[str, str], lookup: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Answered planned searches, by category and by line of business. An answered search that returned nothing still
    counts; a search the engine did not answer does not. A line of business counts only for a query aimed at it."""
    lookup = lookup or {}
    counts: dict[str, int] = defaultdict(int)
    scoped: set[tuple[str, str]] = set()
    queries = pool.answered_searches(run_id)
    for row in queries:
        category = _query_category(row, names)
        counts[category] += 1
        lob = row.get("canonical_lob") or lookup.get(str(row.get("target_id") or ""))
        if lob:
            scoped.add((str(lob), category))
    return {"counts": dict(counts), "scoped": scoped, "queries": queries}


def _country_entities(pack: Any) -> dict[str, list[tuple[str, str]]]:
    """Country -> (entity name, entity id) for every entity registered or holding assets there."""
    from grit.pipeline.reader_scan import graph_pack

    found: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in (graph_pack(pack).exposure or {}).get("entities") or []:
        for country in dict.fromkeys([row.get("countryOfIncorporation"), *(row.get("assetCountries") or [])]):
            if country:
                found[str(country).strip()].append((str(row["entityName"]), str(row.get("entityId") or "")))
    return dict(found)


def _names_country(country: str, text: str) -> bool:
    names = (country, *_COUNTRY_ALIASES.get(country.casefold(), ()))
    return any(re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text, 0 if name.isupper() else re.IGNORECASE) for name in names)


def _mentions(country: str, row: Mapping[str, Any]) -> bool:
    from grit.ontology.registry_loader import slugify

    if f"PLACE-{slugify(country)}" in set((row.get("link") or {}).get("place_ids") or []):
        return True
    return _names_country(country, str(row.get("title") or ""))


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
            "now": f"{clean_title(event['lead'].get('title'))} ({event['last_date'] or 'date not stated'})" if event["members"] else "No report this week.",
            # No report this week is not easing (URS methodology, section 15), so it carries no arrow.
            "direction": f"{UP} New" if event["band"] == UP and event["new"] else f"{UP} Worse" if event["band"] == UP else "No new report",
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
    scope = _searched(pool, run_id, names, lookup)
    searched, scoped = scope["counts"], scope["scoped"]
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
                specific = lob == BA_WIDE or (lob, category) in scoped
                text = NOT_SEARCHED if not searched.get(category) else NIL if specific else NIL_WIDE
                line["cells"][category] = {"text": text, "signal_ids": [], "more": 0}
        coverage.append(line)

    # 6. Jurisdictions. A nil needs a search that named the country or one of its registered entities.
    jurisdictions = []
    up_ids = {signal_id for event in events if event["band"] == UP for signal_id in event["signal_ids"]}
    for country, rows in sorted(_country_entities(pack).items(), key=lambda pair: (-len(pair[1]), pair[0])):
        entities = [name for name, _ in rows]
        entity_ids = {entity_id for _, entity_id in rows if entity_id}
        found = [row for row in shown if _mentions(country, row)]
        found.sort(key=lambda item: (item.get("tier") or 9, -(item.get("corroboration") or 1)))
        country_searched = any(str(query.get("target_id") or "") in entity_ids or _names_country(country, str(query.get("query") or ""))
                               for query in scope["queries"])
        jurisdictions.append({
            "country": country, "entities": entities[:6], "more_entities": max(0, len(entities) - 6),
            "finding": clean_title(found[0].get("title")) if found else NO_FINDING if country_searched else NO_COUNTRY_SEARCH,
            "signal_ids": [found[0]["signal_id"]] if found else [], "searched": country_searched,
            "trend": UP if any(row["signal_id"] in up_ids for row in found) else SAME if found else "–",
        })

    # 7. Named supplier, customer and partner watch.
    # A passing mention says nothing about the item, so it stays in the exposure checks and out of the report.
    watch = [{"name": item["name"], "type": kind, "text": item["text"], "status": item["status"]}
             for kind, items in named.items() for item in items if item.get("text") and item.get("status") != "NOTHING_FOUND"]
    passing = sum(1 for items in named.values() for item in items if item.get("text") and item.get("status") == "NOTHING_FOUND")
    targets = {str(row.get("target_id")) for row in scope["queries"] if row.get("lane") in {"SUPPLIER", "CUSTOMER", "COMPETITOR", "ENTITY"}}
    total = sum(len(items) for items in named.values())
    not_searched = sum(1 for items in named.values() for item in items if item.get("item_id") not in targets and not item.get("text"))

    # 8. Quality: the completeness gate and conflicts.
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
    lines = [lob for lob in lobs if lob != BA_WIDE]
    unswept = [lob for lob in lines if not any((lob, category) in scoped for category in CATEGORIES)
               and not any(cell["signal_ids"] for line in coverage if line["lob"] == lob for cell in line["cells"].values())]
    unsearched = sum(1 for line in coverage for cell in line["cells"].values() if cell["text"] == NOT_SEARCHED)
    wide_only = sum(1 for line in coverage for cell in line["cells"].values() if cell["text"] == NIL_WIDE)
    unsearched_countries = [row["country"] for row in jurisdictions if row["finding"] == NO_COUNTRY_SEARCH]
    competitor_names = [item for item in named.get("Competitor", [])]

    def answer(ok: bool, partly: bool = False) -> str:
        return "Yes" if ok else "Partly" if partly else "No"

    gate = [
        {"question": "Checked macro and financial items (rates, FX, inflation), not just operations?", "answer": answer(bool(searched.get(FINANCIAL))), "critical": True,
         "note": f"{searched.get(FINANCIAL, 0)} financial searches were answered." if searched.get(FINANCIAL) else "No financial search was answered this week."},
        {"question": "Searched every line of business?", "answer": answer(not unswept, len(unswept) < len(lines)), "critical": True,
         "note": f"No search aimed at, and no item for: {', '.join(unswept)}. Only business-area-wide searches cover them." if unswept
         else "Every line of business had an aimed search or an item."},
        {"question": "Checked competitors?", "answer": answer(bool(searched.get(COMPETITOR))), "critical": True,
         "note": f"{searched.get(COMPETITOR, 0)} competitor searches were answered; {sum(1 for item in competitor_names if item.get('text'))} named competitors had an item."},
        {"question": "Checked export controls, sanctions and the supply chain?", "answer": answer(bool(searched.get(REGULATORY)) and bool(searched.get(SUPPLY)), bool(searched.get(REGULATORY) or searched.get(SUPPLY))),
         "critical": True, "note": f"Regulatory searches {searched.get(REGULATORY, 0)}; supply and cost searches {searched.get(SUPPLY, 0)}."},
        {"question": "Searched every jurisdiction with a registered entity?", "answer": answer(not unsearched_countries, len(unsearched_countries) < len(jurisdictions)),
         "critical": True, "note": f"No country-specific search for: {', '.join(unsearched_countries[:12])}{' and more' if len(unsearched_countries) > 12 else ''}."
         if unsearched_countries else "Every jurisdiction had a search naming it or one of its entities."},
        {"question": "Is every nil backed by a search of its own scope?", "answer": answer(unsearched == 0 and wide_only == 0, unsearched == 0), "critical": False,
         "note": f"{wide_only} cells rest on business-area-wide searches only and {unsearched} were not searched. Each cell says which." if wide_only or unsearched
         else "Every nil cell had a search aimed at its line of business."},
        {"question": "Checked the named suppliers and customers?", "answer": answer(not_searched == 0, total > not_searched), "critical": False,
         "note": f"{total - not_searched} of {total} named items were searched or reported this week; the rest rotate on the scan plan's cadence."},
        {"question": "Do the sources conflict?", "answer": "Yes" if conflicts else "No", "critical": False,
         "note": f"{len(conflicts)} conflicts are listed below." if conflicts else "No conflicting figures found within an event."},
    ]
    completeness = {
        "blocking": [row["question"] for row in gate if row["critical"] and row["answer"] == "No"],
        "limitations": [f"{row['question']} {row['answer']}: {row['note']}" for row in gate
                        if row["question"] != "Do the sources conflict?" and row["answer"] != "Yes"],
    }

    # Status, arrows and the risk window.
    red = any(event["red"] for event in events)
    status = "RED" if red else "ELEVATED" if elevated or rising else "STABLE"
    # The status line names what moved: ▲ events, and standing risks whose trend eased. An event without a report this week has not eased.
    arrows = [f"{label(event)} {UP}" for event in events if event["band"] == UP][:4] + [f"{row['name']} {DOWN}" for row in conditions if row["trend"] == "Easing"][:2]
    window = [f"{item['date'].day} {item['date'].strftime('%b')}: {short_text(item['event'])}" for item in look[:2]]

    # Source tiers for every signal the brief may cite.
    tiers = {}
    for row in routed:
        best = None
        for register in registers:
            found = credibility_lookup(str(row.get("url") or ""), register)
            if best is None or found.get("credibilityRating") in {"HIGH", "MEDIUM", "LOW"}:
                best = found
        tiers[row["signal_id"]] = source_tier(row.get("url"), (best or {}).get("credibilityRating"), row.get("publisher"), (best or {}).get("sourceClass"))
    # Outlets R&A has not banded, so a reader knows which reports need verification and R&A knows what to band next.
    from collections import Counter

    from grit.core.source_bands import host_of

    shown = [row for row in routed if row.get("shown")]
    counts = Counter(host_of(row.get("url"), row.get("publisher")) for row in shown if tiers.get(row["signal_id"]) == "UNRATED")
    unrated = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    listed = ", ".join(domain for domain, _ in unrated[:10]) + ("" if len(unrated) <= 10 else ", and others")
    banding = {"question": "Are the cited outlets banded by R&A?", "critical": False,
               "answer": "Yes" if not unrated else "No" if sum(counts.values()) == len(shown) else "Partly",
               "note": "Every cited outlet has an R&A band." if not unrated else
                       f"{len(unrated)} cited outlets have no R&A band yet, so their reports need verification: {listed}."}
    gate.append(banding)
    if unrated:
        completeness["limitations"].append(f"{banding['question']} {banding['answer']}: {banding['note']}")
    # Internal intelligence first (URS methodology, section 4): say whether an approved exposure snapshot was supplied.
    exposure = (run.get("config") or {}).get("exposure_snapshot")
    supplied = {"question": "Was approved internal exposure data supplied?", "critical": False, "answer": "Yes" if exposure else "No",
                "note": (f"Exposure snapshot as of {exposure.get('asOf')}, approved by {exposure.get('approvedBy')}." if exposure else
                         "No approved exposure snapshot this week, so staff, site and project exposure rests on public sources only.")}
    gate.append(supplied)
    if not exposure:
        completeness["limitations"].append(f"{supplied['question']} {supplied['answer']}: {supplied['note']}")
    return {
        "status": status, "arrows": arrows, "risk_window": window, "escalating": escalating_rows, "changes": changes,
        "look_ahead": [{**item, "date": item["date"].isoformat()} for item in look], "priorities": priorities, "coverage": coverage, "categories": list(CATEGORIES),
        "jurisdictions": jurisdictions, "watch": watch, "watch_note": {"total": total, "not_searched": not_searched, "passing": passing}, "gate": gate, "conflicts": conflicts,
        "completeness": completeness,
        "tiers": tiers, "short": short, "unrated_outlets": [{"domain": domain, "signals": count} for domain, count in unrated],
        "exposure": exposure,
    }
