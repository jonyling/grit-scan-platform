"""The reader scan: one reader pack, one week. Every stage reads and writes the pool, so a stage can be rerun alone.

  plan      the reader pack's queries: standing items (conditions or themes), management requests, named items, hazards,
            and watchpoints. A business-area pack takes its named items from the typed graph and rotates them.
  acquire   Researcher: Brave News, plus up to two Currents pulls. A query already answered in this run is not sent again.
  embed     Gemini embeddings into the pool, through the shared vector cache.
  assess    deterministic: document type, link ladder, reader-pack hits, keep rule, priority, risk cascade, harm scorer,
            relevance labels, and the RED rule. No model.
  agents    Assessor drafts each standing item, Critic argues, Reporter writes item entries and the bottom line.
            Checks decide what is valid. Watchpoints are updated by rule.
  findings  the six-question finding loop for the strongest register-linked signals.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping, Sequence

from grit.acquisition.brave_plan import without_navigation
from grit.core.confidence import evidence_confidence, trend_label
from grit.core.doc_type import classify
from grit.core.reader_filter import KEEP, ReaderFilterConfig, filter_reader
from grit.core.red_alert import red_check
from grit.core.scale_cues import scale_cues
from grit.core.wild_scan_relevance import classify_wild_scan_risk_level, compose_wild_scan_basis
from grit.ontology.graph import build_graph
from grit.ontology.link import LinkIndex, link_candidate
from grit.ontology.matchers.credibility_lookup import credibility_lookup
from grit.ontology.pack import OntologyPack, load_ontology_pack
from grit.core.routing import match_request
from grit.ontology.reader_index import CONDITION, CUSTOMER, HAZARD, PROJECT, SITE, SUPPLIER, WATCHPOINT, ReaderHit, ReaderIndex
from grit.ontology.reader_plan import reader_queries, target_of
from grit.ontology.risk_cascade import load_risk_cascade
from grit.pipeline.harm import harm_verdict, opportunity_status, score_harm
from grit.pool import Pool
from grit.settings import CONFIG_DIR, OUTPUT_DIR

ENTITY, COMPETITOR, REQUEST = "ENTITY", "COMPETITOR", "REQUEST"
EXPOSURE_TYPES = (PROJECT, SUPPLIER, CUSTOMER, SITE, ENTITY, COMPETITOR)
_GRAPH_TYPES = {"Customer": CUSTOMER, "Supplier": SUPPLIER, "Org": COMPETITOR, "Site": SITE, "STE": ENTITY}
TIER_LABELS = {"KEY": "Key risk", "TIER_2": "Tier-2 risk", "EMERGING": "Emerging risk", "KEY_RISK": "Key risk", "TIER2_RISK": "Tier-2 risk", "EMERGING_RISK": "Emerging risk"}
MAX_CONDITION_SOURCES = 12
MAX_ITEM_SOURCES = 4


@dataclass(frozen=True)
class ReaderRun:
    run_id: str
    run_date: date
    pack: str

    @property
    def folder(self) -> Path:
        return OUTPUT_DIR / "runs" / self.run_id


def run_id_for(run_date: date, pack: str) -> str:
    return f"{run_date.isoformat()}-{pack}"


def _load_pack(pack: str) -> OntologyPack:
    return load_ontology_pack(CONFIG_DIR, pack)


def _aliases() -> dict[str, Any]:
    return json.loads((CONFIG_DIR / "registry_aliases.json").read_text(encoding="utf-8-sig"))


def graph_pack(pack: OntologyPack) -> OntologyPack:
    """The business-area pack whose typed graph binds names: the pack itself at BA level, else its parent. A Group pack binds
    names through all its business-area children: this returns a view with their registers joined (spec 37)."""
    if pack.level == "BA":
        return pack
    if pack.level == "GROUP":
        import dataclasses

        children = [child for child in (_load_pack(name) for name in pack.children) if child.level == "BA"]
        if not children:
            raise ValueError(f"Group pack {pack.pack} names no business-area children in pack.json.")
        exposure = {key: [row for child in children for row in (child.exposure.get(key) or [])] for key in ("entities", "customers", "suppliers", "projects", "sites")}
        lobs = {"lobs": [lob for child in children for lob in (child.lob_profiles.get("lobs") or [])]}
        competitors = {"competitors": [row for child in children for row in ((child.competitors or {}).get("competitors") or [])]}
        return dataclasses.replace(pack, exposure=exposure, lob_profiles=lobs, competitors=competitors)
    parent = (pack.manifest or {}).get("parent")
    if not parent:
        raise ValueError(f"Pack {pack.pack} is {pack.level} level and names no parent business-area pack in pack.json.")
    return _load_pack(parent)


def pack_graph(pack: OntologyPack) -> Any:
    """The typed graph a reader pack binds names through: its business area's graph, or for a Group pack the merged graphs
    of its business-area children, with the Group pack's own themes."""
    from grit.ontology.graph import merge_graphs

    if pack.level == "GROUP":
        children = [child for child in (_load_pack(name) for name in pack.children) if child.level == "BA"]
        return merge_graphs((build_graph(child, reviewed_aliases=_aliases()) for child in children), themes=(pack.themes or {}).get("themes") or [])
    return build_graph(graph_pack(pack), reviewed_aliases=_aliases())


def footprint_rule(link: Any, hits: Sequence[ReaderHit], graph: Any) -> Any:
    """Group packs only (spec 37). A signal with no register link that names a country where an STE entity is registered, in
    the title or lead, and strongly hits a standing condition, is on the FOOTPRINT rung. A country alone is still no link."""
    import dataclasses

    from grit.ontology.link import FOOTPRINT

    if link.rung is not None:
        return link
    conditions = [hit for hit in hits if hit.item_type == CONDITION and hit.strong]
    places = [place for place in link.place_ids if graph.entities_in_place(place)]
    if not conditions or not places:
        return link
    place = graph.nodes[places[0]]
    reason = f"FOOTPRINT: names {place.name}, where {len(graph.entities_in_place(places[0]))} registered STE entities sit, and hits standing condition {conditions[0].item_name}."
    return dataclasses.replace(link, rung=FOOTPRINT, reasons=(*link.reasons, reason))


def standing_set(pack: OntologyPack) -> str:
    return str((pack.scan_plan or {}).get("standingSet") or ("conditions" if (pack.baseline or {}).get("conditions") else "themes"))


def standing_items(pack: OntologyPack) -> list[dict[str, Any]]:
    """What the Assessor drafts each week. An entity pack's standing conditions, or a business-area pack's risk themes."""
    if standing_set(pack) == "conditions":
        return [
            {"id": row["id"], "name": row["name"], "kind": "CONDITION", "domain": row.get("domain"), "currentAssessment": row.get("currentAssessment"),
             "transmission": row.get("taiwanTransmission") or row.get("transmission"), "watchIndicators": row.get("watchIndicators") or [], "coveredBy": None}
            for row in pack.baseline.get("conditions") or []
        ]
    plan = {row["themeId"]: row for row in (pack.scan_plan or {}).get("themes") or []}
    return [
        {"id": row["themeId"], "name": row["themeName"], "kind": "THEME", "domain": TIER_LABELS.get(row.get("tier"), row.get("tier")), "tier": row.get("tier"),
         "currentAssessment": row.get("sheetDetail"), "transmission": row.get("sheetExtra"), "watchIndicators": row.get("keywords") or [],
         "coveredBy": (plan.get(row["themeId"]) or {}).get("coveredBy"), "actionPlans": row.get("sheetActionPlans")}
        for row in (pack.themes or {}).get("themes") or []
    ]


def seed_requests(pool: Pool, pack: OntologyPack) -> int:
    """Copy the scan plan's management requests into the pool. Wording, keywords and queries follow the config. Status is
    state: the config sets it once, and after that only a reviewer changes it."""
    added = 0
    for row in (pack.scan_plan or {}).get("managementRequests") or []:
        values = {
            "title": row["title"], "kind": row.get("kind") or "TOPIC", "keywords_json": json.dumps(row.get("keywords") or []), "queries_json": json.dumps(row.get("queries") or []),
            "country": row.get("country"), "lob": row.get("lob"), "pack": pack.pack, "source": row.get("source"),
        }
        if pool.one("SELECT 1 FROM management_requests WHERE request_id = ?", (row["requestId"],)):
            with pool.transaction() as conn:
                conn.execute(
                    "UPDATE management_requests SET title = ?, kind = ?, keywords_json = ?, queries_json = ?, country = ?, lob = ?, pack = ?, source = ? WHERE request_id = ?",
                    (*values.values(), row["requestId"]),
                )
            continue
        pool.insert("management_requests", {
            "request_id": row["requestId"], "description": None, "requested_by": row.get("requestedBy") or f"{pack.label} management", "request_date": row.get("requestDate") or "2026-10-06",
            "status": row.get("status") or "PROPOSED", "origin": "WORKBOOK", "profile_id": None, **values,
        })
        added += 1
    return added


# Plan ------------------------------------------------------------------------------------------------------------


def seed_watchpoints(pool: Pool, pack: OntologyPack) -> int:
    """Copy the scan plan's watchpoints into the pool. Title, detail and keywords follow the config. Status and the count of
    quiet cycles are state, so the pool keeps them: a watchpoint a person closed stays closed."""
    added = 0
    for row in (pack.scan_plan or {}).get("watchpoints") or []:
        keywords = {"subject": row.get("keywords") or [], "issue": row.get("issueKeywords") or []}
        if pool.one("SELECT 1 FROM watchpoints WHERE watchpoint_id = ?", (row["watchpointId"],)):
            with pool.transaction() as conn:
                conn.execute("UPDATE watchpoints SET title = ?, detail = ?, keywords_json = ? WHERE watchpoint_id = ?", (row["title"], row["detail"], json.dumps(keywords), row["watchpointId"]))
            continue
        pool.insert(
            "watchpoints",
            {
                "watchpoint_id": row["watchpointId"], "pack": pack.pack, "title": row["title"], "detail": row["detail"], "keywords_json": keywords,
                "status": row.get("status") or "OPEN", "opened_on": row["openedOn"], "cycles_without_update": 0,
            },
        )
        added += 1
    return added


def plan(pool: Pool, run: ReaderRun) -> list[Any]:
    pack = _load_pack(run.pack)
    if not pack.scan_plan:
        raise ValueError(f"Pack {run.pack} has no scan plan. Write config/{run.pack}/PREFIX-Scan-Plan.json and name it as scan_plan under "
                         f"optional in its pack.json (docs/add-a-business-area.md, 'Write the scan plan').")
    seed_watchpoints(pool, pack)
    seed_requests(pool, pack)
    requests = [row for row in pool.management_requests(active_only=True) if row.get("pack") in (None, run.pack)]
    return reader_queries(pack.scan_plan, pool.watchpoints(run.pack), requests, pack=pack, reviewed_aliases=_aliases(), run_date=run.run_date)


# Acquire ---------------------------------------------------------------------------------------------------------


def acquire(
    pool: Pool,
    run: ReaderRun,
    specs: Sequence[Any],
    *,
    limit: int | None = None,
    getter: Callable[..., Any] | None = None,
    currents_queries: Sequence[str] = (),
    currents_getter: Callable[..., Any] | None = None,
    usage_path: Path | None = None,
    sleep: Callable[[float], None] | None = None,
    log: Callable[[str], None] = print,
    fallback_max: int | None = None,
) -> dict[str, Any]:
    """Researcher. Sends each query once per run, writes every result into the pool as it returns, and logs every search.
    A query Brave did not answer (the monthly limit, a rate limit, or an error) or answered with nothing goes to Currents,
    up to fallback_max per run (spec 37)."""
    from grit.acquisition.brave import BraveFetchError
    from grit.acquisition.brave_plan import candidates_from_searches, collect_plan

    run.folder.mkdir(parents=True, exist_ok=True)
    answered = {row["query_id"] for row in pool.rows("SELECT DISTINCT query_id FROM sightings WHERE run_id = ?", (run.run_id,))}
    answered |= {entry["query_id"] for entry in _entries(run.folder / "searches.jsonl") if entry.get("query_id")}
    todo = [spec for spec in specs if spec.query_id not in answered]
    if limit is not None:
        todo = _balanced(todo, limit)
    by_id = {spec.query_id: spec for spec in specs}
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = {"queries_sent": 0, "results": 0, "signals_new": 0, "skipped_answered": len(specs) - len([s for s in specs if s.query_id not in answered])}
    returned: dict[str, int] = {}

    def on_search(entry: dict[str, Any]) -> None:
        with (run.folder / "searches.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        candidates, hits = candidates_from_searches([entry], fetched_at=entry["fetched_at"])
        spec = by_id[entry["query_id"]]
        engine = entry.get("engine") or "BRAVE"
        returned[spec.query_id] = returned.get(spec.query_id, 0) + len(entry.get("results") or [])
        for candidate in candidates:
            before = pool.one("SELECT 1 FROM signals WHERE url = ?", (candidate.url,))
            signal_id = pool.add_signal(
                url=candidate.url, title=candidate.title, text=candidate.text, publisher=candidate.publisher, published_at=candidate.published_at,
                lane=engine, content_hash=candidate.content_hash, seen_at=entry["fetched_at"],
            )
            found["signals_new"] += 0 if before else 1
            for hit in hits.get(candidate.url, []):
                pool.add_sighting(signal_id, run.run_id, query_id=spec.query_id, query=spec.query, lane=spec.lane, target_id=target_of(spec), rank=hit.get("rank"))
        found["queries_sent"] += 1
        found["results"] += len(entry.get("results") or [])
        log(f"  {spec.lane:10} {len(entry.get('results') or []):>3} results  {spec.query}" + ("  (Currents)" if engine == "CURRENTS" else ""))

    stopped = None
    if todo:
        options: dict[str, Any] = {"usage_path": usage_path} if usage_path else {}
        if sleep is not None:
            options["sleep"] = sleep
        try:
            result = collect_plan(todo, fetched_at=fetched_at, freshness=_freshness(run), country="ALL", getter=getter, on_search=on_search, **options)
            stopped = result.stopped
        except BraveFetchError as exc:
            stopped = "BRAVE_ERROR"
            log(f"  Brave stopped: {exc}")
        thin = [spec for spec in todo if returned.get(spec.query_id, 0) == 0]
        # An injected Brave getter is an offline run: Currents runs live only beside a live Brave, or with its own getter.
        if thin and (getter is None or currents_getter is not None):
            found["currents_fallback"] = _currents_fallback(thin, fetched_at=fetched_at, getter=currents_getter, on_search=on_search, limit=fallback_max, log=log)
    if currents_queries:
        found["currents"] = _currents(pool, run, currents_queries[:2], currents_getter, fetched_at)
    from grit.copilot.web import take_additions

    added = take_additions(pool, run.run_id, run.pack)
    if added:
        found["added_from_copilot"] = added
        log(f"  {'ADDED':10} {added:>3} pages from the daily lane and the Co-Pilot")
    found["stopped"] = stopped
    pool.log(actor_type="AGENT", actor="Researcher", status="SUCCEEDED" if not stopped else stopped, run_id=run.run_id, version="brave-news-1", detail=found)
    return found


WEEKLY_WINDOW = 7


def scan_window(pack: OntologyPack) -> int:
    """Days a run covers, counting the run date: 7 for a weekly pack, the scan plan's windowDays for a monthly one."""
    days = int((pack.scan_plan or {}).get("windowDays") or WEEKLY_WINDOW)
    return days if days > 10 else WEEKLY_WINDOW


def _freshness(run: ReaderRun) -> str:
    """Brave's date range: exactly the run's window, ending on the run date, so a run dated in the past gets that week's
    news and not today's."""
    days = scan_window(_load_pack(run.pack)) if run.pack else WEEKLY_WINDOW
    start = run.run_date - timedelta(days=days - 1)
    return f"{start.isoformat()}to{run.run_date.isoformat()}"


def _currents_fallback(
    specs: Sequence[Any], *, fetched_at: str, getter: Callable[..., Any] | None, on_search: Callable[[dict[str, Any]], None], limit: int | None, log: Callable[[str], None]
) -> dict[str, Any]:
    """Send Brave's unanswered and empty queries to Currents, one search each, logged like a Brave search (spec 37)."""
    import os

    from grit.acquisition.currents import CurrentsFetchError, collect_currents

    cap = limit if limit is not None else int(os.environ.get("CURRENTS_FALLBACK_MAX", "40") or 40)
    if getter is None and not os.environ.get("CURRENTS_API_KEY", "").strip():
        log(f"  Currents fallback skipped for {len(specs)} queries: CURRENTS_API_KEY is not set.")
        return {"sent": 0, "skipped": len(specs), "reason": "NO_KEY"}
    sent = 0
    for spec in list(specs)[:cap]:
        try:
            candidates = collect_currents(spec.query, fetched_at=fetched_at, max_records=20, getter=getter)
        except CurrentsFetchError as exc:
            log(f"  Currents stopped: {exc}")
            return {"sent": sent, "skipped": len(specs) - sent, "reason": "CURRENTS_ERROR"}
        results = [
            {"url": item.url, "title": item.title, "text": item.text, "publisher": item.publisher, "published_at": item.published_at, "rank": rank}
            for rank, item in enumerate(candidates, start=1) if item.url and item.title
        ]
        on_search({"query_id": spec.query_id, "query": spec.query, "sent_query": spec.query, "engine": "CURRENTS", "status": 200, "fetched_at": fetched_at, "results": results})
        sent += 1
    return {"sent": sent, "skipped": max(0, len(specs) - cap), "reason": None if len(specs) <= cap else "FALLBACK_LIMIT"}


def _currents(pool: Pool, run: ReaderRun, queries: Sequence[str], getter: Callable[..., Any] | None, fetched_at: str) -> int:
    from grit.acquisition.currents import collect_currents

    added = 0
    for query in queries:
        for candidate in collect_currents(query, fetched_at=fetched_at, max_records=20, getter=getter):
            if not candidate.url or not candidate.title:
                continue
            signal_id = pool.add_signal(url=candidate.url, title=candidate.title, text=candidate.text, publisher=candidate.publisher, published_at=candidate.published_at, lane="CURRENTS", content_hash=candidate.content_hash)
            pool.add_sighting(signal_id, run.run_id, query_id=f"currents-{query}", query=query, lane="CURRENTS", target_id=None, rank=None)
            added += 1
    return added


def _balanced(specs: Sequence[Any], count: int) -> list[Any]:
    lanes: dict[str, list[Any]] = defaultdict(list)
    for spec in specs:
        lanes[spec.lane].append(spec)
    chosen: list[Any] = []
    while len(chosen) < count and any(lanes.values()):
        for lane in list(lanes):
            if lanes[lane] and len(chosen) < count:
                chosen.append(lanes[lane].pop(0))
    return chosen


def _entries(path: Path) -> list[dict[str, Any]]:
    """Every complete line of a JSON-lines log. A half-written last line from an interrupted run is skipped."""
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


# Embed -----------------------------------------------------------------------------------------------------------


def signal_text(signal: Mapping[str, Any]) -> str:
    return f"{signal.get('title') or ''}\n{signal.get('text') or ''}".strip()


def embed(pool: Pool, run: ReaderRun, *, embedder: Any | None = None) -> dict[str, int]:
    from grit.retrieval.embedder import Embedder
    from grit.retrieval.store import VectorStore, text_hash

    embedder = embedder or Embedder()
    signals = pool.signals_for_run(run.run_id)
    have = pool.vectors([s["signal_id"] for s in signals], model=embedder.model, task="RETRIEVAL_DOCUMENT", dimensions=embedder.dimensions)
    missing = [signal for signal in signals if signal["signal_id"] not in have]
    store = VectorStore(OUTPUT_DIR / "vector" / "index.sqlite")
    try:
        vectors = store.vectors([signal_text(signal) for signal in missing], task="RETRIEVAL_DOCUMENT", embedder=embedder) if missing else []
    finally:
        store.close()
    for signal, vector in zip(missing, vectors):
        pool.put_vector(signal["signal_id"], model=embedder.model, task="RETRIEVAL_DOCUMENT", dimensions=embedder.dimensions, text_hash=text_hash(signal_text(signal)), vector=vector)
    result = {"signals": len(signals), "cached": len(have), "embedded": len(missing)}
    pool.log(actor_type="FUNCTION", actor="Embed", status="SUCCEEDED", run_id=run.run_id, model=embedder.model, detail=result)
    return result


def run_vectors(pool: Pool, run: ReaderRun) -> dict[str, list[float]]:
    from grit.retrieval.embedder import DEFAULT_DIMENSIONS, DEFAULT_MODEL

    ids = [signal["signal_id"] for signal in pool.signals_for_run(run.run_id)]
    return pool.vectors(ids, model=DEFAULT_MODEL, task="RETRIEVAL_DOCUMENT", dimensions=DEFAULT_DIMENSIONS)


# Assess ----------------------------------------------------------------------------------------------------------


def _credibility(url: str, outlet: str | None, registers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    for register in registers:
        result = credibility_lookup(url, register)
        if best is None or (best.get("credibilityRating") in {None, "UNRATED"} and result.get("credibilityRating") not in {None, "UNRATED"}):
            best = result
    return {**(best or {}), "url": url, "outlet": outlet}


def assess(pool: Pool, run: ReaderRun, *, red_patterns: Mapping[str, Any] | None = None, score_harm_fn: Callable[..., dict[str, Any]] = score_harm) -> dict[str, Any]:
    """Deterministic only. Writes one assessment row per signal in the run."""
    reader = _load_pack(run.pack)
    business = graph_pack(reader)
    graph = pack_graph(reader)
    index = LinkIndex(graph, business.lob_profiles)
    seed_watchpoints(pool, reader)
    seed_requests(pool, reader)
    from_graph = ((reader.scan_plan or {}).get("namedItems") or {}).get("source") == "graph"
    readers = ReaderIndex({} if from_graph else reader.exposure, reader.scan_plan, reader.baseline, pool.watchpoints(run.pack))
    requests = [row for row in pool.management_requests(active_only=True) if row.get("pack") in (None, run.pack)]
    themes_in_plan = {item["id"] for item in standing_items(reader)} if standing_set(reader) == "themes" else set()
    cascade = load_risk_cascade(pack=reader)
    from grit.core.red_events import merge_red_patterns

    patterns = red_patterns if red_patterns is not None else merge_red_patterns(json.loads((CONFIG_DIR / "red-patterns.json").read_text(encoding="utf-8")))
    conditions = {condition["id"]: condition for condition in reader.baseline.get("conditions") or []}
    registers = [reader.source_register, business.source_register]
    window = scan_window(reader)

    signals = pool.signals_for_run(run.run_id)
    records: list[dict[str, Any]] = []
    sightings = defaultdict(set)
    for row in pool.sightings(run.run_id):
        if row.get("target_id"):
            sightings[row["signal_id"]].add(row["target_id"])
    for signal in signals:
        signal = {**signal, "text": without_navigation(signal["text"])}
        candidate = {"title": signal["title"], "text": signal["text"], "publisher": signal["publisher"], "url": signal["url"], "published_at": signal["published_at"]}
        doc = classify(candidate)
        pool.set_doc_type(signal["signal_id"], doc.doc_type)
        link = link_candidate(candidate, index, business.lob_profiles)
        if from_graph:
            link = generic_name_rule(link, f"{signal['title']}\n{signal['text']}", (reader.scan_plan.get("namedItems") or {}).get("genericNames"), graph)
        hits = readers.hits(signal["title"], signal["text"])
        if from_graph:
            hits += graph_hits(link, graph)
        if reader.level == "GROUP":
            link = footprint_rule(link, hits, graph)
        if themes_in_plan:
            # A theme keyword is common prose. It counts for a theme only on a page that theme's own query found, or on a page
            # that already links to the register.
            hits += [
                ReaderHit(CONDITION, theme.theme_id, theme.theme_id, ", ".join(theme.matched), theme.where, len(theme.matched), True)
                for theme in link.themes
                if theme.strong and theme.theme_id in themes_in_plan and (theme.theme_id in sightings[signal["signal_id"]] or link.rung)
            ]
        request = match_request({**candidate, "pack": run.pack}, requests, [run.pack])
        if request:
            hits.append(ReaderHit(REQUEST, request[0], request[0], request[1], "LEAD", 1, True))
        records.append({**signal, "doc_type": doc, "link": link, "hits": hits, "scale": scale_cues(candidate)})
    decisions = filter_reader(records, run_date=run.run_date, vectors=run_vectors(pool, run), config=ReaderFilterConfig(window_days=window))

    kept = red = 0
    for record in records:
        signal_id = record["signal_id"]
        decision = decisions[signal_id]
        link = record["link"]
        hits = record["hits"]
        strong = [hit for hit in hits if hit.strong]
        condition_ids = sorted({hit.item_id for hit in strong if hit.item_type == CONDITION})
        exposure = [hit.as_record() for hit in hits if hit.item_type in EXPOSURE_TYPES]
        hazards = [hit.as_record() for hit in strong if hit.item_type == HAZARD]
        watch = [hit.as_record() for hit in strong if hit.item_type == WATCHPOINT]
        row: dict[str, Any] = {
            "signal_id": signal_id, "run_id": run.run_id, "pack": run.pack, "decision": decision["decision"], "drop_reason": decision["reason"],
            "rung": decision["rung"], "link_json": {**link.as_record(), "reader_rung_reason": decision["rung_reason"], "reader_hits": [hit.as_record() for hit in hits],
                                                   "hazards": hazards, "watchpoints": watch, "sighting_targets": sorted(sightings[signal_id]),
                                                   "duplicate_of": decision["duplicate_of"], "also_reported_by": decision["also_reported_by"], "doc_cues": list(record["doc_type"].cues)},
            "themes_json": [{"theme_id": theme.theme_id, "tier": theme.tier, "matched": list(theme.matched), "where": theme.where, "strong": theme.strong} for theme in link.themes],
            "condition_ids_json": condition_ids, "exposure_json": exposure, "priority_json": decision["priority"] or {},
            "tier": (decision["priority"] or {}).get("tier"), "corroboration": decision["corroboration"],
        }
        if decision["decision"] == KEEP:
            kept += 1
            outlets = [{"url": record["url"], "publisher": record["publisher"]}, *decision["also_reported_by"]]
            credibility = [_credibility(item["url"], item.get("publisher"), registers) for item in outlets]
            harm = score_harm_fn(record, run_date=run.run_date, credibility=credibility, lob=link.canonical_lob, pack=run.pack)
            risks = cascade.hits(record["title"], record["text"], [theme.theme_id for theme in link.themes if theme.strong])
            own_level = [{"themeId": hit.risk_id, "themeName": hit.name, "tier": hit.tier} for hit in risks if hit.level == reader.level]
            condition = conditions.get(condition_ids[0]) if condition_ids else None
            high_rated = any(item.get("credibilityRating") == "HIGH" for item in credibility)
            verdict = harm_verdict(harm)
            red_result = red_check({**record, "rung": decision["rung"], "harm_verdict": verdict}, pack=run.pack, patterns=patterns, corroboration=decision["corroboration"], high_rated_source=high_rated)
            red += int(red_result.red)
            row |= {
                "risk_cascade_json": [hit.as_record() for hit in risks], "harm_json": harm, "harm_verdict": verdict, "opportunity_cost": opportunity_status(harm),
                "trend": "NO_MATERIAL_CHANGE", "risk_level": classify_wild_scan_risk_level(own_level),
                "relevance_basis": compose_wild_scan_basis(condition, publisher=record["publisher"]) if condition else _basis(decision, link, exposure),
                "evidence_confidence": evidence_confidence(credibility)[0], "red_rule_json": red_result.as_record(), "red": int(red_result.red),
            }
        pool.save_assessment(row)
    result = {"signals": len(records), "kept": kept, "red": red, "dropped": _count(decisions)}
    pool.log(actor_type="FUNCTION", actor="ReaderAssess", status="SUCCEEDED", run_id=run.run_id, version="reader-assess-1", detail=result)
    return result


def generic_name_rule(link: Any, text: str, rule: Mapping[str, Any] | None, graph: Any = None) -> Any:
    """A generic public-body name ('MINISTRY OF DEFENCE') counts only when the page names one of the pack's places. Without
    one, the name stays recorded as a passing mention, and the rung falls back down the link ladder: a registered competitor,
    then a LOB keyword with a place where that LOB has an entity, then a LOB keyword alone."""
    import dataclasses
    import re

    if not rule:
        return link
    generic = re.compile(rule["pattern"], re.IGNORECASE)
    cued = any(re.search(rf"(?<![\w]){re.escape(cue)}(?![\w])", text, 0 if cue.isupper() else re.IGNORECASE) for cue in rule.get("cues") or [])
    if cued:
        return link
    changed = [item for item in link.involves if item.strong and item.cls in {"Customer", "Supplier"} and generic.search(item.name)]
    if not changed:
        return link
    involves = tuple(dataclasses.replace(item, strong=False) if item in changed else item for item in link.involves)
    reasons = [*link.reasons, *(f"Not a link: '{item.matched}' is a generic public-body name, and the page names none of the pack's places." for item in changed)]
    rung = link.rung
    if rung == "COUNTERPARTY" and not any(item.strong and item.cls in {"Customer", "Supplier"} for item in involves):
        near = [place for place in link.place_ids if graph is not None and graph.entities_in_place(place) & graph.entities_in_lob(link.lob_id)] if link.lob_id else []
        rung = "COMPETITOR" if any(item.strong and item.cls == "Org" for item in involves) else "PERIMETER" if near else "SECTOR" if link.lob_id else None
        reasons.append(f"Rung falls back to {rung or 'no link'}.")
    return dataclasses.replace(link, involves=involves, rung=rung, reasons=tuple(reasons))


def graph_hits(link: Any, graph: Any) -> list[ReaderHit]:
    """A business-area pack's named items are the register names the link ladder found, with its strength rules."""
    found = []
    for item in link.involves:
        item_type = _GRAPH_TYPES.get(item.cls)
        if item_type is None:
            continue
        found.append(ReaderHit(item_type, item.node_id, item.name, item.matched, item.where, item.count, bool(item.strong)))
    return found


def _basis(decision: Mapping[str, Any], link: Any, exposure: Sequence[Mapping[str, Any]]) -> str:
    reasons = [decision["rung_reason"]] if decision.get("rung_reason") else []
    reasons += list(link.reasons[:2])
    named = [f"{hit['item_type'].lower()} {hit['item_name']}" for hit in exposure if hit.get("strong")]
    if named:
        reasons.append("Names " + ", ".join(named[:3]) + ".")
    return " ".join(reasons) or "Theme-level relevance only. No register link is established."


def _count(decisions: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for decision in decisions.values():
        if decision["decision"] != KEEP:
            counts[str(decision["reason"])] += 1
    return dict(sorted(counts.items()))


# Agents ----------------------------------------------------------------------------------------------------------


def _kept(pool: Pool, run: ReaderRun) -> list[dict[str, Any]]:
    return pool.assessments(run.run_id, kept_only=True)


def _recent_first(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    rows = list(rows)
    rows.sort(key=lambda row: str(row.get("published_at") or ""), reverse=True)
    rows.sort(key=lambda row: (row.get("tier") or 9, -(row.get("corroboration") or 1)))
    return rows


def material_order(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Briefing order for findings: a management-request match first, then a signal bound to a standing item, then the
    rest, each by tier, outlets and recency. Deterministic; it decides what the reader sees first, never a fact."""
    rows = list(rows)
    rows.sort(key=lambda row: str(row.get("published_at") or ""), reverse=True)
    rows.sort(key=lambda row: (
        not any(hit.get("item_type") == REQUEST for hit in (row.get("link") or {}).get("reader_hits") or []),
        row.get("tier") or 9,
        not row.get("condition_ids"),
        -(row.get("corroboration") or 1),
    ))
    return rows


_MATERIAL_RUNGS = {"DIRECT": 0, "PERIMETER": 1, "COUNTERPARTY": 2}


def _request_hit(row: Mapping[str, Any]) -> bool:
    return any(hit.get("item_type") == REQUEST for hit in (row.get("link") or {}).get("reader_hits") or [])


def materiality_order(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The evidence order for a standing item (spec 36): RED or a high-negative-impact harm verdict, then the link rung,
    a management-request match, briefing tier, outlets, and recency. Deterministic; no model judges materiality."""
    rows = list(rows)
    rows.sort(key=lambda row: str(row.get("published_at") or ""), reverse=True)
    rows.sort(key=lambda row: (
        not (row.get("red") or row.get("harm_verdict") == "HIGH_NEGATIVE_IMPACT"),
        _MATERIAL_RUNGS.get(str(row.get("rung") or (row.get("link") or {}).get("rung")), 3),
        not _request_hit(row),
        row.get("tier") or 9,
        -(row.get("corroboration") or 1),
    ))
    return rows


def is_material(row: Mapping[str, Any]) -> bool:
    """A source that can carry a key risk's response options: a register link, a request match, briefing tier 1 or 2, or RED."""
    rung = str(row.get("rung") or (row.get("link") or {}).get("rung"))
    return rung in _MATERIAL_RUNGS or _request_hit(row) or (row.get("tier") or 9) <= 2 or bool(row.get("red"))


def condition_sources(rows: Sequence[Mapping[str, Any]], condition_id: str, *, theme: bool = False) -> list[Mapping[str, Any]]:
    """Kept signals that bear on a standing item: a strong keyword hit, or found by one of the item's own queries. For a risk
    theme the most material come first (spec 36). An entity pack's standing conditions keep the tier-then-recency order."""
    chosen = [row for row in rows if condition_id in (row.get("condition_ids") or []) or condition_id in (row["link"].get("sighting_targets") or [])]
    return (materiality_order(chosen) if theme else _recent_first(chosen))[:MAX_CONDITION_SOURCES]


def child_sources(pool: Pool, run: ReaderRun, child: str) -> list[Mapping[str, Any]]:
    """For a theme an entity pack covers: that pack's kept signals on the same date that name its projects, suppliers or
    customers, or reach briefing tier 2."""
    rows = pool.assessments(run_id_for(run.run_date, child), kept_only=True)
    chosen = [row for row in rows if any(hit.get("strong") for hit in row.get("exposure") or []) or (row.get("tier") or 9) <= 2]
    return materiality_order(chosen)[:MAX_CONDITION_SOURCES]


def _prior(pool: Pool, run: ReaderRun) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]]]:
    previous = pool.latest_run(run.pack, before=run.run_date.isoformat(), kind="READER_SCAN")
    if previous is None:
        return None, {}
    return previous, {row["condition_id"]: row for row in pool.conditions(previous["run_id"])}


# Owners the Proposer may name, besides the LOB and grouping heads in the LOB hierarchy. Roles, never people.
GROUP_FUNCTIONS = (
    "Business area head", "R&A business partner", "Group Risk & Assurance", "Group Information Security Officer (GISO)",
    "Export control and trade compliance office", "Data protection officer", "Group Procurement", "Human Resources", "Legal and Compliance", "Finance",
)


def business_area_context(pack: OntologyPack) -> dict[str, Any]:
    """What a key risk's implication may name (spec 36): the business area's lines of business, groupings, entities, customers
    and suppliers from the typed graph. The model sees one short name for each. The check accepts any alias."""
    business = graph_pack(pack)
    graph = pack_graph(pack)
    own = {business.label.casefold(), str((business.manifest or {}).get("shortLabel") or "").casefold(), "st engineering", "ste"}

    def short(node: Any) -> str:
        names = [alias for alias in node.aliases if len(alias) >= 4 and alias.casefold() not in own]
        return min(names, key=len) if names else node.name

    groups = {
        "lines_of_business": [node for node in graph.of_class("LOB")],
        "entities": [node for node in graph.of_class("STE") if node.kind == "ENTITY"],
        "customers": list(graph.of_class("Customer")),
        "suppliers": list(graph.of_class("Supplier")),
    }
    if pack.level == "GROUP":
        # The Group view also names the business areas and the countries where STE entities sit (spec 37).
        groups["business_areas"] = [node for node in graph.of_class("STE") if node.kind == "BUSINESS_AREA"]
        groups["countries"] = [node for node in graph.of_class("Place") if graph.entities_in_place(node.id)]
    anchors = sorted({alias for nodes in groups.values() for node in nodes for alias in (node.name, *node.aliases) if len(alias) >= 3 and alias.casefold() not in own})
    context = {"name": business.label, **{key: sorted({short(node) for node in nodes}) for key, nodes in groups.items()}, "anchors": anchors}
    if pack.level == "GROUP":
        context["group"] = True
    return context


def owner_roles(pack: OntologyPack) -> list[str]:
    """The LOB and grouping heads from the hand-authored LOB hierarchy, then the Group functions."""
    hierarchy = graph_pack(pack).lob_hierarchy or {}
    roles = [str(group.get("headRole") or f"Head, {group['lobGrouping']}") for group in hierarchy.get("groupings") or [] if group.get("lobGrouping")]
    roles += [f"Head, {lob['lobName']}" for lob in hierarchy.get("lobs") or [] if lob.get("lobName")]
    return list(dict.fromkeys([*roles, *GROUP_FUNCTIONS]))


def run_agents(pool: Pool, run: ReaderRun, *, client: Any, model: str, log: Callable[[str], None] = print) -> dict[str, Any]:
    from grit.agents import reader as agents
    from grit.core.conditions import import_scan, rank, revise

    pack = _load_pack(run.pack)
    items = standing_items(pack)
    by_id = {item["id"]: item for item in items}
    rows = _kept(pool, run)
    previous_run, priors = _prior(pool, run)
    with pool.transaction() as conn:
        conn.execute("DELETE FROM qa_log WHERE run_id = ?", (run.run_id,))  # derived per run; a rerun of the agents writes it again
    registers = [pack.source_register, graph_pack(pack).source_register]
    by_signal = {row["signal_id"]: row for row in rows}

    def write_log(entry: agents.CallLog) -> None:
        pool.log(actor_type="AGENT", run_id=run.run_id, **entry.as_record())

    def prior_view(key: str) -> dict[str, Any] | None:
        prior = priors.get(key)
        return {"trend": prior.get("trend_label"), "summary": prior.get("summary"), "run_date": previous_run["run_date"]} if prior and previous_run else None

    # Assessor: one draft per standing item. A theme an entity pack covers draws on that pack's signals for the same date.
    # A key risk also gets an implication for the business area (spec 36).
    # A Group pack's conditions get it too, with the opportunity and the counter-indicator (spec 37).
    group = pack.level == "GROUP"
    area = business_area_context(pack) if standing_set(pack) == "themes" or group else None

    def area_for(item: Mapping[str, Any]) -> dict[str, Any] | None:
        return area if area is not None and (item.get("tier") == "KEY" or group) else None

    drafts: dict[str, dict[str, Any]] = {}
    tables: dict[str, tuple[dict[str, dict[str, Any]], dict[str, str]]] = {}
    for item in items:
        sources = child_sources(pool, run, item["coveredBy"]) if item.get("coveredBy") else condition_sources(rows, item["id"], theme=item["kind"] == "THEME")
        by_signal.update({row["signal_id"]: row for row in sources})
        table, back = agents.source_table(sources)
        tables[item["id"]] = (table, back)
        draft, call = agents.assess_condition(client, model, condition=item, prior=prior_view(item["id"]), sources=table, business_area=area_for(item))
        write_log(call)
        drafts[item["id"]] = draft
        log(f"  Assessor {item['id']:16} {draft['status']:12} {draft.get('movement') or '-':20} sources {len(table)}")

    # Critic: one pass over every validated draft, against last week's text and the same sources.
    combined, combined_back = _combined_table(tables, drafts)
    critic_view = [
        {"item_id": key, "name": by_id[key]["name"], "movement": draft.get("movement"), "summary": _renumber(draft.get("summary") or "", tables[key][1], combined_back)}
        for key, draft in drafts.items() if draft["status"] == "VALIDATED"
    ]
    accepted: list[dict[str, Any]] = []
    if critic_view:
        accepted, rejected, call = agents.critique(client, model, drafts=critic_view, priors={key: str(row.get("summary") or "") for key, row in priors.items()}, sources=combined)
        write_log(call)
        log(f"  Critic   issues accepted {len(accepted)}, rejected {len(rejected)}")
    for issue in accepted:
        key = str(issue["item_id"])
        table, back = tables[key]
        source_signal = combined_back.get(str(issue.get("source_id")))
        local = next((sid for sid, signal in back.items() if signal == source_signal), None)
        if local is None:
            local = f"S{len(table) + 1}"
            table[local] = combined[str(issue["source_id"])]
            back[local] = str(source_signal)
        fixed_issue = dict(issue) | {"source_id": local, "correction": _renumber(str(issue.get("correction") or ""), combined_back, back)}
        item = by_id[key]
        refined, call = agents.assess_condition(client, model, condition=item, prior=prior_view(key), sources=table, issues=[fixed_issue], previous=drafts[key], business_area=area_for(item))
        write_log(call)
        before = drafts[key].get("summary")
        if refined["status"] in {"VALIDATED", "NO_EVIDENCE"}:
            drafts[key] = refined
            pool.add_qa(run.run_id, item=f"{key}: {item['name']}", prior=str(issue.get("problem") or before), correction=agents.strip_marks(fixed_issue["correction"]), source_ids=[str(source_signal)], origin="CRITIC")
        else:
            pool.add_qa(run.run_id, item=f"{key}: {item['name']}", prior=str(issue.get("problem")), correction="Raised by the Critic. The redraft failed its checks, so the earlier draft stands.", source_ids=[str(source_signal)], origin="CRITIC")

    # Proposer: options for a key risk whose validated assessment is escalating or cites a material source (spec 36).
    options: dict[str, list[dict[str, Any]]] = {}
    if area is not None:
        owners = owner_roles(pack)
        for item in items:
            key = item["id"]
            draft = drafts[key]
            if (item.get("tier") != "KEY" and not group) or draft["status"] != "VALIDATED":
                continue
            table, back = tables[key]
            cited = [ref for ref in draft.get("source_ids") or [] if ref in table]
            if draft.get("movement") != "ESCALATING" and not any(is_material(by_signal[back[ref]]) for ref in cited if back.get(ref) in by_signal):
                log(f"  Proposer {key:16} SKIPPED      no escalation and no material source")
                continue
            # A Group condition has no register of key controls: its transmission text is not a control, so it is not offered as one.
            theme = {**item, "transmission": None} if group else item
            result, call = agents.propose_options(
                client, model, theme=theme, assessment={"movement": draft.get("movement"), "summary": draft.get("summary"), "implication": draft.get("implication")},
                sources={ref: table[ref] for ref in cited}, owners=owners,
            )
            write_log(call)
            log(f"  Proposer {key:16} {result['status']:12} options {len(result['options'])}")
            if result["status"] == "VALIDATED":
                options[key] = [{**option, "source_ids": [back[ref] for ref in option["source_ids"] if ref in back]} for option in result["options"]]
            else:
                pool.add_qa(run.run_id, item=f"{key}: {item['name']}", prior=None, correction="Response options withheld: the Proposer's draft failed its checks twice. " + "; ".join(result["violations"][:2]), origin="CHECK")

    # Persist the standing items. For conditions, run the governed weekly cascade for themes, descent and priority.
    scan: list[dict[str, Any]] = []
    for item in items:
        key = item["id"]
        draft = drafts[key]
        table, back = tables[key]
        cited = [back[ref] for ref in draft.get("source_ids") or [] if ref in back]
        credibility = [_credibility(by_signal[sid]["url"], by_signal[sid]["publisher"], registers) for sid in cited if sid in by_signal]
        level, _reason = evidence_confidence(credibility)
        summary = _cite(draft.get("summary") or "", back) if draft.get("summary") else None
        status = draft["status"]
        if status == "WITHHELD":
            pool.add_qa(run.run_id, item=f"{key}: {item['name']}", prior=None, correction="Withheld: the Assessor's draft failed its checks twice. " + "; ".join(draft["violations"][:2]), origin="CHECK")
        if draft.get("implication_violations"):
            pool.add_qa(run.run_id, item=f"{key}: {item['name']}", prior=None, correction="Implication, opportunity or counter-indicator withheld: it failed its checks twice, so the assessment stands without it. " + "; ".join(draft["implication_violations"][:2]), origin="CHECK")
        pool.save_condition({
            "run_id": run.run_id, "pack": run.pack, "condition_id": key, "condition_name": item["name"], "movement": draft.get("movement"),
            "trend_label": trend_label(draft.get("movement")) if status != "WITHHELD" else "Not established", "confidence": level,
            "summary": summary, "source_ids_json": cited, "status": status, "violations_json": draft.get("violations") or [],
            "implication": _cite(draft["implication"], back) if draft.get("implication") else None, "options_json": options.get(key, []),
            "opportunity": _cite(draft["opportunity"], back) if draft.get("opportunity") else None, "counter_indicator": draft.get("counter_indicator"),
            "ranking_json": {"kind": item["kind"], "tier": item.get("tier"), "domain": item.get("domain"), "coveredBy": item.get("coveredBy")},
        })
        if status == "VALIDATED" and item["kind"] == "CONDITION":
            scan.append({
                "conditionHint": key, "movement": draft["movement"], "evidenceConfidence": level or "LOW", "summary": agents.strip_marks(draft["summary"]),
                "sources": [{"url": by_signal[sid]["url"]} for sid in cited if sid in by_signal],
            })
    if standing_set(pack) == "conditions":
        revision = revise(import_scan(json.dumps(scan), pack.baseline, revision_id=run.run_id, revision_date=run.run_date.isoformat()), pack)
        ranking = {item["conditionId"]: item for item in rank(revision, pack)["rankedItems"]}
        for item in revision["revisions"]:
            pool.conn.execute(
                "UPDATE condition_assessments SET ranking_json = ? WHERE run_id = ? AND condition_id = ?",
                (json.dumps({"kind": "CONDITION", **item, **ranking.get(item["conditionId"], {})}, default=str), run.run_id, item["conditionId"]),
            )
        pool.conn.commit()

    checks = _item_checks(pool, run, pack, rows, client=client, model=model, write_log=write_log, log=log)
    watch = update_watchpoints(pool, run, rows)
    bottom = _bottom_line(pool, run, client=client, model=model, write_log=write_log)
    from grit.pipeline.events import draft_rows

    events = draft_rows(pool, run, client=client, model=model, log=log)
    return {
        "events": events,
        "standing": {key: draft["status"] for key, draft in drafts.items()}, "critic_accepted": len(accepted),
        "implications": sum(1 for draft in drafts.values() if draft.get("implication")), "options": {key: len(rows) for key, rows in options.items()},
        "items": checks, "watchpoints": watch, "bottom_line": bottom["status"],
    }


def _combined_table(tables: Mapping[str, tuple[dict[str, Any], dict[str, str]]], drafts: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, str]]:
    """One numbering across conditions for the Critic, so it can compare drafts that share a source."""
    combined: dict[str, Any] = {}
    back: dict[str, str] = {}
    seen: dict[str, str] = {}
    for key, (table, local) in tables.items():
        for source_id, entry in table.items():
            signal_id = local[source_id]
            if signal_id in seen:
                continue
            new = f"S{len(combined) + 1}"
            combined[new], back[new], seen[signal_id] = entry, signal_id, new
    return combined, back


def _renumber(text: str, source_back: Mapping[str, str], target_back: Mapping[str, str]) -> str:
    """Rewrite [S#] marks from one numbering to another through the signal ids."""
    import re

    reverse = {signal: key for key, signal in target_back.items()}

    def swap(match: re.Match[str]) -> str:
        # A mark with no source in the target numbering is dropped: its old number may name a different source there.
        parts = [reverse[source_back[part.strip()]] for part in match.group(1).split(",") if source_back.get(part.strip()) in reverse]
        return "[" + ", ".join(parts) + "]" if parts else ""

    return re.sub(r"\[(S\d{1,3}(?:\s*,\s*S\d{1,3})*)\]", swap, text)


def _cite(text: str, back: Mapping[str, str]) -> str:
    """Store marks as signal ids, so the renderer can print the outlet and date for each."""
    import re

    def swap(match: re.Match[str]) -> str:
        return "[" + ", ".join(back.get(part.strip(), part.strip()) for part in match.group(1).split(",")) + "]"

    return re.sub(r"\[(S\d{1,3}(?:\s*,\s*S\d{1,3})*)\]", swap, text)


ITEM_BATCH = 15


def _item_rows(pack: OntologyPack, rows: Sequence[Mapping[str, Any]] = ()) -> list[tuple[str, str, str]]:
    """Every named item the pack checks each week. A graph-sourced pack lists its STE nodes, customers and suppliers, and
    only the competitors that this week's signals name: 191 quiet competitors are not news."""
    plan = pack.scan_plan or {}
    if (plan.get("namedItems") or {}).get("source") == "graph":
        graph = pack_graph(pack)
        found = [(ENTITY, node.id, node.name) for node in graph.of_class("STE")]
        found += [(CUSTOMER, node.id, node.name) for node in graph.of_class("Customer")]
        found += [(SUPPLIER, node.id, node.name) for node in graph.of_class("Supplier")]
        named = {hit["item_id"]: hit["item_name"] for row in rows for hit in row.get("exposure") or [] if hit.get("item_type") == COMPETITOR}
        found += [(COMPETITOR, node_id, name) for node_id, name in sorted(named.items(), key=lambda pair: pair[1])]
        return found
    exposure = pack.exposure
    found = [(PROJECT, row["projectId"], row["projectName"]) for row in exposure.get("projects") or []]
    found += [(SUPPLIER, row["supplierId"], row["supplierName"]) for row in exposure.get("suppliers") or []]
    found += [(CUSTOMER, row["customerId"], row["customerName"]) for row in exposure.get("customers") or []]
    found += [(CUSTOMER, row["itemId"], row.get("name") or row["itemId"]) for row in plan.get("customers") or [] if row.get("aliases")]
    found += [(HAZARD, row["itemId"], row.get("name") or row["itemId"]) for row in plan.get("hazards") or []]
    return found


def _item_checks(pool: Pool, run: ReaderRun, pack: OntologyPack, rows: Sequence[Mapping[str, Any]], *, client: Any, model: str, write_log: Callable[[Any], None], log: Callable[[str], None]) -> dict[str, int]:
    """Every named item gets a row. Strong hits go to the Reporter, in batches. Passing mentions are listed by rule. None is NOTHING_FOUND."""
    from grit.agents import reader as agents

    strong_by_item: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    weak_by_item: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        for hit in row.get("exposure") or []:
            (strong_by_item if hit.get("strong") else weak_by_item)[hit["item_id"]].append(row)
        for hit in row["link"].get("hazards") or []:
            strong_by_item[hit["item_id"]].append(row)
    item_rows = _item_rows(pack, rows)
    with pool.transaction() as conn:
        conn.execute("DELETE FROM exposure_checks WHERE run_id = ?", (run.run_id,))  # derived per run; the item list can change
    pending = [(item_type, item_id, name) for item_type, item_id, name in item_rows if strong_by_item.get(item_id)]
    results: dict[str, dict[str, Any]] = {}
    backs: dict[str, dict[str, str]] = {}
    for start in range(0, len(pending), ITEM_BATCH):
        sources: list[Mapping[str, Any]] = []
        index: dict[str, str] = {}
        requests: list[dict[str, Any]] = []
        for item_type, item_id, name in pending[start : start + ITEM_BATCH]:
            ids = []
            for row in _recent_first(strong_by_item[item_id])[:MAX_ITEM_SOURCES]:
                if row["signal_id"] not in index:
                    sources.append(row)
                    index[row["signal_id"]] = f"S{len(sources)}"
                ids.append(index[row["signal_id"]])
            requests.append({"item_id": item_id, "item_type": item_type, "name": name, "source_ids": ids})
        table, back = agents.source_table(sources)
        batch, call = agents.report_items(client, model, items=requests, sources=table)
        write_log(call)
        results.update(batch)
        backs.update({item_id: back for item_id in batch})
    counts: dict[str, int] = defaultdict(int)
    for item_type, item_id, name in item_rows:
        result = results.get(item_id)
        weak = weak_by_item.get(item_id, [])
        if result and result["status"] == "VALIDATED":
            back = backs[item_id]
            status, summary, cited = "FOUND", _cite(result["summary"], back), [back[s] for s in result["source_ids"] if s in back]
        elif result:
            status, summary, cited = "WITHHELD", None, []
            pool.add_qa(run.run_id, item=f"{item_type.title()}: {name}", correction="Withheld: the Reporter's entry failed its checks. " + "; ".join(result["violations"][:2]), origin="CHECK")
        elif weak:
            titles = "; ".join(f"\u201c{row['title']}\u201d [{row['signal_id']}]" for row in _recent_first(weak)[:2])
            status, summary, cited = "NOTHING_FOUND", f"Passing mention only, with nothing specific to {pack.label}: {titles}.", [row["signal_id"] for row in weak[:2]]
        else:
            status, summary, cited = "NOTHING_FOUND", None, []
        counts[status] += 1
        pool.save_exposure_check({"run_id": run.run_id, "pack": run.pack, "item_type": item_type, "item_id": item_id, "item_name": name, "signal_ids_json": cited, "summary": summary, "status": status})
    log(f"  Reporter items {dict(counts)}")
    return dict(counts)


def update_watchpoints(pool: Pool, run: ReaderRun, rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """By rule: a kept signal that strongly names a watchpoint's keywords is an update. Otherwise the count of quiet cycles grows."""
    updated = quiet = 0
    for watchpoint in pool.watchpoints(run.pack):
        earlier = pool.one("SELECT cycles_before FROM watchpoint_updates WHERE watchpoint_id = ? AND run_id = ?", (watchpoint["watchpoint_id"], run.run_id))
        # A rerun of the same week starts from the count before that week, so the quiet cycles neither double nor reset.
        before = int(earlier["cycles_before"]) if earlier else int(watchpoint["cycles_without_update"] or 0)
        hits = [row for row in rows if any(hit["item_id"] == watchpoint["watchpoint_id"] for hit in row["link"].get("watchpoints") or [])]
        hits = _recent_first(hits)[:3]
        if hits:
            updated += 1
            note = "New items this week: " + "; ".join(f"\u201c{row['title']}\u201d [{row['signal_id']}]" for row in hits) + "."
            pool.conn.execute("UPDATE watchpoints SET last_evidence_on = ?, last_checked_run = ?, cycles_without_update = 0 WHERE watchpoint_id = ?", (run.run_date.isoformat(), run.run_id, watchpoint["watchpoint_id"]))
        else:
            quiet += 1
            cycles = before + 1
            note = f"No new item this week. Unconfirmed for {cycles} consecutive weekly cycle(s)."
            pool.conn.execute("UPDATE watchpoints SET last_checked_run = ?, cycles_without_update = ? WHERE watchpoint_id = ?", (run.run_id, cycles, watchpoint["watchpoint_id"]))
        pool.conn.commit()
        pool.insert("watchpoint_updates", {"watchpoint_id": watchpoint["watchpoint_id"], "run_id": run.run_id, "signal_ids_json": [row["signal_id"] for row in hits], "note": note, "cycles_before": before}, replace=True)
    return {"updated": updated, "quiet": quiet}


def request_matches(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    """Kept signals per management request, by the deterministic keyword rule recorded at assessment."""
    found: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        for hit in row["link"].get("reader_hits") or []:
            if hit.get("item_type") == REQUEST:
                found[hit["item_id"]].append(row)
    return found


def child_summaries(pool: Pool, run: ReaderRun, pack: OntologyPack) -> list[dict[str, Any]]:
    """The entity packs under a business area, as run on the same date: their bottom line and what moved."""
    found = []
    for child in pack.children:
        child_run = pool.run(run_id_for(run.run_date, child))
        if child_run is None:
            found.append({"pack": child, "label": _load_pack(child).label, "run_id": None, "bottom_line": None, "moved": []})
            continue
        bottom = (child_run.get("config") or {}).get("bottom_line") or {}
        moved = [f"{row['condition_name']}: {row['trend_label'].lower()}" for row in pool.conditions(child_run["run_id"]) if row["trend_label"] in {"Escalating", "Easing"}]
        found.append({
            "pack": child, "label": _load_pack(child).label, "run_id": child_run["run_id"], "moved": moved,
            "bottom_line": bottom.get("bottom_line") if bottom.get("status") == "VALIDATED" else None, "escalation": bottom.get("escalation") if bottom.get("status") == "VALIDATED" else None,
        })
    return found


def bottom_line_facts(pool: Pool, run: ReaderRun) -> dict[str, Any]:
    from grit.agents.checks import strip_marks

    pack = _load_pack(run.pack)
    rows = _kept(pool, run)
    requests = {row["request_id"]: row for row in pool.management_requests() if row.get("pack") in (None, run.pack)}
    matches = request_matches(rows)
    return {
        "brief": f"{pack.label} {'monthly' if int((pack.scan_plan or {}).get('windowDays') or 8) > 10 else 'weekly'} risk scan ({pack.level.lower()} level)",
        "reader": (pack.scan_plan or {}).get("audience") or pack.label,
        "standing_items": [
            {"name": row["condition_name"], "kind": (row.get("ranking") or {}).get("kind") or "CONDITION", "trend": row["trend_label"], "confidence": row["confidence"], "summary": strip_marks(row["summary"] or "") or None}
            for row in pool.conditions(run.run_id)
        ],
        "management_requests": [
            {"title": requests[key]["title"], "status": requests[key]["status"], "signals_this_week": [item["title"] for item in matches.get(key, [])[:3]]}
            for key in requests if requests[key]["status"] == "ACTIVE"
        ],
        "entity_packs": child_summaries(pool, run, pack),
        "named_items": [{"type": row["item_type"], "name": row["item_name"], "status": row["status"], "summary": strip_marks(row["summary"] or "") or None} for row in pool.exposure_checks(run.run_id) if row["status"] != "NOTHING_FOUND" or row["summary"]],
        "named_items_with_nothing_found": [row["item_name"] for row in pool.exposure_checks(run.run_id) if row["status"] == "NOTHING_FOUND" and not row["summary"]][:60],
        "watchpoints": [
            {"title": row["title"], "status": row["status"], "detail": row["detail"], "quiet_cycles": row["cycles_without_update"],
             "this_week": (pool.one("SELECT note FROM watchpoint_updates WHERE watchpoint_id = ? AND run_id = ?", (row["watchpoint_id"], run.run_id)) or {}).get("note")}
            for row in pool.watchpoints(run.pack)
        ],
        "red_alert_drafts": [row["title"] for row in rows if row["red"]],
        "qa_corrections": [row["correction"] for row in pool.qa_log(run.run_id) if row["origin"] == "CRITIC"],
    }


def _bottom_line(pool: Pool, run: ReaderRun, *, client: Any, model: str, write_log: Callable[[Any], None]) -> dict[str, Any]:
    from grit.agents import reader as agents

    result, call = agents.bottom_line(client, model, facts=bottom_line_facts(pool, run))
    write_log(call)
    pool.update_run_config(run.run_id, bottom_line=result)
    return result


# Findings --------------------------------------------------------------------------------------------------------


def findings(pool: Pool, run: ReaderRun, *, client: Any, model: str, top: int = 6, max_tier: int = 2, search: Callable[..., Any] | None = None, log: Callable[[str], None] = print) -> dict[str, Any]:
    """The six-question card for the strongest register-linked signals. Signals linked only through the reader pack have no
    business-area graph path, so the loop is not run on them; their reader-pack entries carry them instead."""
    from grit.agents.loop.engine import FindingLoop, LoopConfig
    from grit.agents.loop.tools import FollowUpTool

    reader = _load_pack(run.pack)
    business = graph_pack(reader)
    graph = pack_graph(reader)
    index = LinkIndex(graph, business.lob_profiles)
    rows = material_order(row for row in _kept(pool, run) if row["link"].get("rung") and (row.get("tier") or 9) <= max_tier)[:top]
    vectors_by_id = run_vectors(pool, run)
    every = pool.signals_for_run(run.run_id)
    by_url = {signal["url"]: signal for signal in every}
    vectors = {signal["url"]: vectors_by_id[signal["signal_id"]] for signal in every if signal["signal_id"] in vectors_by_id}
    pool_records = [{"url": s["url"], "title": s["title"], "text": s["text"], "publisher": s["publisher"], "published_at": s["published_at"]} for s in every]
    tool = FollowUpTool(search=search, embed=None, index=index, pool=pool_records, vectors=vectors, same_event=0.90, budget=top)
    names = (business.label, str((business.manifest or {}).get("shortLabel") or business.label))
    engine = FindingLoop(client, model, graph=graph, index=index, themes=business.themes["themes"], pool=pool_records, vectors=vectors, tool=tool, config=LoopConfig(), business_area=names)
    written = 0
    for number, row in enumerate(rows, start=1):
        signal = {
            "url": row["url"], "title": row["title"], "text": row["text"], "publisher": row["publisher"], "published_at": row["published_at"],
            "link": {key: value for key, value in row["link"].items() if key in {"rung", "involves", "lob_id", "canonical_lob", "lob_keywords", "place_ids", "themes", "reasons"}},
            "rung": row["link"].get("rung"), "priority": row["priority"], "doc_type": row.get("doc_type"),
            "also_reported_by": [{"url": item["url"], "publisher": item.get("publisher")} for item in row["link"].get("also_reported_by") or [] if item.get("url") in by_url],
        }
        card = engine.run(f"F{number:02d}", signal)
        card["signal_id"] = row["signal_id"]
        pool.save_finding(run.run_id, card, row["signal_id"])
        pool.log(actor_type="AGENT", actor="Assessor+Critic (finding loop)", status=card["loop"]["stop"], run_id=run.run_id, model=model, subject=row["signal_id"],
                 detail={"finding_id": card["finding_id"], "calls": card["loop"]["model_calls"], "confidence": card["q6"]["level"]})
        written += 1
        log(f"  Finding F{number:02d} {card['status']:9} {card['q6']['level']:15} {row['title'][:60]}")
    return {"findings": written}


def new_run(pool: Pool, run_date: date, pack: str, *, config: Mapping[str, Any] | None = None) -> ReaderRun:
    run = ReaderRun(run_id_for(run_date, pack), run_date, pack)
    if pool.run(run.run_id) is None:
        pool.start_run(run.run_id, run_date=run_date.isoformat(), pack=pack, kind="READER_SCAN", config=config)
    run.folder.mkdir(parents=True, exist_ok=True)
    return run


def stub_client(responses: Callable[[str, dict[str, Any]], dict[str, Any]]) -> Any:
    """A client for tests and dry runs: responses(system, payload) returns the JSON the model would."""

    class _Models:
        def generate_content(self, *, model: str, contents: str, config: dict[str, Any]) -> Any:
            return SimpleNamespace(text=json.dumps(responses(config["system_instruction"], json.loads(contents))))

    return SimpleNamespace(models=_Models())
