"""grit adverse: the weekly Adverse News & Lessons Learnt Scan (spec 46).

Stages: plan and search, screen by rule, Screener, group and verify, Assessor, corroborate, Critic, decide and render.
Every dependency that touches the network or a model can be injected, so the tests run offline.
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from grit.adverse import agents, checks, render
from grit.adverse.registers import PlannedQuery, Registers, business_areas, load_registers
from grit.adverse.screen import Candidate, Item, corroboration, group, in_window, match_previous, screen, similar
from grit.pool import Pool
from grit.pool.db import utc_now

FIRST_WINDOW_DAYS = 7
PREVIOUS_DAYS = 120
ASSESS_CAPS = {"A": 30, "B": 20, "C": 20}
MAX_SOURCES = 4
MAX_SOURCE_CHARS = 8000
PACK = "adverse"


MIN_PAGE_CHARS = 400
_CHALLENGE = ("client challenge", "verify you are human", "enable javascript", "checking your browser", "captcha", "access denied", "are you a robot")


def readable(text: str) -> bool:
    """A page with enough text that is not a bot check. A short page that names a challenge is not the article."""
    folded = text.casefold()
    return len(text) >= MIN_PAGE_CHARS and not (len(text) < 3000 and any(marker in folded for marker in _CHALLENGE))


def scan_id_for(run_date: date) -> str:
    return f"{run_date.isoformat()}-{PACK}"


def output_folder(run_date: date) -> Path:
    from grit.settings import OUTPUT_DIR

    return OUTPUT_DIR / "adverse" / run_date.isoformat()


def reporting_window(pool: Pool, run_date: date, since: date | None = None) -> dict[str, Any]:
    """From the end of the latest READY scan before the run date, else the seven days to the run date (spec 46, section 3)."""
    previous = pool.one("SELECT * FROM adverse_scans WHERE status = 'READY' AND run_date < ? ORDER BY run_date DESC LIMIT 1", (run_date.isoformat(),))
    if since is not None:
        start = since
    elif previous:
        start = date.fromisoformat(previous["window_end"])
    else:
        start = run_date - timedelta(days=FIRST_WINDOW_DAYS - 1)
    if start > run_date:
        raise ValueError(f"The reporting period would start on {start}, after the run date {run_date}.")
    return {"start": start, "end": run_date, "previous": previous["run_date"] if previous else None}


# Sources ----------------------------------------------------------------------------------------------------------------


def _ratings() -> dict[str, str]:
    from grit.ontology.matchers.credibility_lookup import shared_ratings

    return {str(r["domain"]).lower().removeprefix("www."): str(r.get("rating") or "") for r in shared_ratings().get("ratings") or [] if r.get("domain")}


def band_function(ratings: Mapping[str, str] | None = None) -> Callable[[Any, Any], str]:
    from grit.core.source_bands import host_of, source_band

    table = _ratings() if ratings is None else dict(ratings)

    def band(url: Any, publisher: Any = None) -> str:
        host = host_of(url, publisher)
        rating = next((table[d] for d in (host, *[host.split(".", i)[-1] for i in range(1, host.count(".") + 1)]) if d in table), None)
        return source_band(url, rating, publisher)

    return band


def outlet_of(member: Candidate) -> str:
    from grit.core.source_bands import host_of

    return str(member.publisher or host_of(member.url) or "source")


# Search -----------------------------------------------------------------------------------------------------------------


def _logged(folder: Path) -> list[dict[str, Any]]:
    path = folder / "searches.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def search(pool: Pool, scan_id: str, specs: Sequence[PlannedQuery], window: Mapping[str, date], folder: Path, *, limit: int,
           getter: Any = None, usage_path: Path | None = None, sleep: Any = None, log: Callable[[str], None] = print) -> dict[str, Any]:
    """Send each unanswered query once, within the limit, and add every result to the pool as it returns."""
    from grit.acquisition.brave import BraveFetchError
    from grit.acquisition.brave_plan import candidates_from_searches, collect_plan

    folder.mkdir(parents=True, exist_ok=True)
    by_id = {spec.query_id: spec for spec in specs}
    answered = {entry.get("query_id") for entry in _logged(folder)}
    todo = [spec for spec in specs if spec.query_id not in answered][:max(0, limit)]
    result: dict[str, Any] = {"planned": len(specs), "skipped_answered": len(answered & set(by_id)), "queries_sent": 0, "results": 0, "stopped": None}

    def on_search(entry: dict[str, Any]) -> None:
        with (folder / "searches.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        spec = by_id[entry["query_id"]]
        pool.record_search(scan_id, query_id=spec.query_id, engine="BRAVE", query=spec.query, lane=spec.lane, target_id=None, canonical_lob=None,
                           results=len(entry.get("results") or []), fetched_at=str(entry["fetched_at"]))
        found, hits = candidates_from_searches([entry], fetched_at=entry["fetched_at"])
        for candidate in found:
            signal_id = pool.add_signal(url=candidate.url, title=candidate.title, text=candidate.text, publisher=candidate.publisher,
                                        published_at=candidate.published_at, lane="BRAVE", content_hash=candidate.content_hash, seen_at=entry["fetched_at"])
            for hit in hits.get(candidate.url, []):
                pool.add_sighting(signal_id, scan_id, query_id=spec.query_id, query=spec.query, lane=spec.lane, target_id=None, rank=hit.get("rank"))
        result["queries_sent"] += 1
        result["results"] += len(entry.get("results") or [])
        log(f"  {spec.lane:10} {len(entry.get('results') or []):>3} results  {spec.query}")

    if todo:
        options: dict[str, Any] = {}
        if usage_path is not None:
            options["usage_path"] = usage_path
        if sleep is not None:
            options["sleep"] = sleep
        fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            run = collect_plan(todo, fetched_at=fetched_at, freshness=f"{window['start'].isoformat()}to{window['end'].isoformat()}", country="ALL",
                               getter=getter, on_search=on_search, **options)
            result["stopped"] = run.stopped
        except BraveFetchError as exc:
            result["stopped"] = f"BRAVE_ERROR: {exc}"
            log(f"  Brave stopped: {exc}")
    pool.log(actor_type="AGENT", actor="Researcher", status="SUCCEEDED" if not result["stopped"] else "STOPPED", run_id=scan_id, version="adverse-1", detail=result)
    return result


def pool_rows(pool: Pool, window: Mapping[str, date]) -> list[dict[str, Any]]:
    return pool.rows(
        """SELECT s.signal_id, s.url, s.title, s.text, s.publisher, s.published_at, s.first_seen_at, m.event_id
           FROM signals s LEFT JOIN event_members m ON m.signal_id = s.signal_id
           WHERE substr(COALESCE(s.published_at, s.first_seen_at), 1, 10) BETWEEN ? AND ?""",
        (window["start"].isoformat(), window["end"].isoformat()),
    )


def previous_items(pool: Pool, scan_id: str, run_date: date) -> list[dict[str, Any]]:
    since = (run_date - timedelta(days=PREVIOUS_DAYS)).isoformat()
    rows = pool.rows(
        """SELECT i.*, s.run_date FROM adverse_items i JOIN adverse_scans s ON s.scan_id = i.scan_id
           WHERE i.status = 'INCLUDED' AND i.scan_id != ? AND s.run_date >= ? AND s.run_date < ? ORDER BY s.run_date DESC""",
        (scan_id, since, run_date.isoformat()),
    )
    for row in rows:
        detail = row.get("item") or {}
        row["evidence"] = " ".join(str(f.get("quote") or "") for f in detail.get("facts") or [])
    return rows


# Assessment -------------------------------------------------------------------------------------------------------------


def verified_sources(item: Item, verify: Callable[[str], Mapping[str, Any]], cache: dict[str, Any], band_rank: Mapping[str, int]
                     ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Up to MAX_SOURCES opened pages for an item: the model's source table, the rendered references, and the failures."""
    table: dict[str, dict[str, Any]] = {}
    refs: list[dict[str, Any]] = []
    failures: list[str] = []
    for member in sorted(item.members, key=lambda m: (band_rank.get(m.band, 9), -m.score)):
        if len(table) >= MAX_SOURCES:
            break
        if member.url not in cache:
            cache[member.url] = dict(verify(member.url))
        result = cache[member.url]
        if result.get("status") == "SUCCEEDED" and not readable(str(result.get("text") or "")):
            result = {"status": "NOT_CHECKED", "reason": "The page returned a bot check or too little text to verify."}
        if result.get("status") != "SUCCEEDED":
            failures.append(f"{outlet_of(member)}: {result.get('reason') or result.get('status')}")
            continue
        key = f"S{len(table) + 1}"
        table[key] = {"title": member.title, "outlet": outlet_of(member), "publication_date": (member.published_at or "")[:10] or "not stated",
                      "band": member.band, "text": str(result.get("text") or "")[:MAX_SOURCE_CHARS]}
        refs.append({"source_id": key, "outlet": outlet_of(member), "url": member.url, "published": (member.published_at or "")[:10] or None,
                     "first_seen": member.first_seen_at[:10], "signal_id": member.signal_id, "band": member.band})
    return table, refs, failures


def register_facts(item: Item, graph: Any, registers: Registers) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for node_id in item.node_ids:
        node = graph.nodes.get(node_id)
        if node is None:
            continue
        entry = registers.entry(node_id) or {}
        facts.append({"name": node.name, "class": node.cls, "kind": node.kind, "business_areas": business_areas(graph, node_id),
                      "relationship": entry.get("relationship") or node.attrs.get("relationship"),
                      "basis": "adverse watchlist (public sources)" if entry else "business-area register"})
    return facts


def ste_named(sources: Mapping[str, Mapping[str, Any]], index: Any) -> bool:
    return any(inv.cls in {"STE", "Site"} for s in sources.values() for inv in index.involvements(str(s.get("title") or ""), str(s.get("text") or "")))


# Run --------------------------------------------------------------------------------------------------------------------


def run_scan(
    run_date: date,
    *,
    pool: Pool,
    client: Any = None,
    model: str = "gemini-2.5-flash",
    registers: Registers | None = None,
    graph_bundle: tuple[Any, Mapping[str, Any]] | None = None,
    getter: Any = None,
    verify: Callable[[str], Mapping[str, Any]] | None = None,
    since: date | None = None,
    limit_queries: int | None = None,
    no_search: bool = False,
    dry_run: bool = False,
    workers: int = 4,
    folder: Path | None = None,
    usage_path: Path | None = None,
    ratings: Mapping[str, str] | None = None,
    sleep: Any = None,
    thinking: str | None = "LOW",
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    from grit.adverse.registers import anchors as anchor_names
    from grit.adverse.registers import group_graph
    from grit.core.publisher_family import registrable
    from grit.core.source_bands import BANDS, host_of
    from grit.ontology.link import COUNTERPARTY, DIRECT, LinkIndex

    registers = registers or load_registers()
    graph, lob_profiles = graph_bundle or group_graph(registers)
    index = LinkIndex(graph, lob_profiles)
    scan_id = scan_id_for(run_date)
    folder = folder or output_folder(run_date)
    window = reporting_window(pool, run_date, since)
    limit = registers.query_limit if limit_queries is None else max(0, int(limit_queries))
    band_of = band_function(ratings)
    band_rank = {band: rank for rank, band in enumerate(BANDS)}
    log(f"Adverse news scan {scan_id}: reporting period {window['start']} to {window['end']}"
        + (f", since the scan of {window['previous']}." if window["previous"] else ", the seven days to the run date (no previous scan)."))

    search_result: dict[str, Any] = {"planned": len(registers.queries), "queries_sent": 0, "skipped_answered": 0, "stopped": None}
    if not dry_run:
        pool.start_run(scan_id, run_date=run_date.isoformat(), pack=PACK, kind="ADVERSE_SCAN", config={"window": {k: str(v) for k, v in window.items()}})
        pool.upsert("adverse_scans", {"scan_id": scan_id, "run_date": run_date.isoformat(), "window_start": window["start"].isoformat(),
                                      "window_end": window["end"].isoformat(), "status": "STARTED", "created_at": utc_now()}, ["scan_id"])
        if no_search:
            search_result["stopped"] = "NOT_RUN: --no-search"
        else:
            log(f"Searching: up to {limit} Brave queries, including corroboration.")
            search_result = search(pool, scan_id, registers.queries, window, folder, limit=limit, getter=getter, usage_path=usage_path, sleep=sleep, log=log)

    lanes: dict[str, list[str]] = {}
    for row in pool.rows("SELECT signal_id, lane FROM sightings WHERE run_id = ?", (scan_id,)):
        lanes.setdefault(row["signal_id"], []).append(str(row["lane"]))
    peer_ids = frozenset(e["id"] for e in registers.watchlist.get("entries") or [] if e.get("cls") == "Org")
    rows = pool_rows(pool, window)
    candidates, counts = screen(rows, index=index, lob_profiles=lob_profiles, lexicon=registers.lexicon, lanes_by_signal=lanes, band_of=band_of,
                                peer_ids=peer_ids, business_areas_of=lambda node_id: business_areas(graph, node_id))
    counts.update({f"candidates_{s}": len(v) for s, v in candidates.items()})
    log(f"Screened {counts['pages']} pages: {len(candidates['A'])} Section A, {len(candidates['B'])} Section B and {len(candidates['C'])} Section C candidates.")
    if dry_run:
        for spec in registers.queries:
            log(f"  {spec.lane:10} {spec.query}")
        return {"status": "DRY_RUN", "window": {k: str(v) for k, v in window.items()}, "counts": counts}
    if client is None:
        from grit.agents.gemini import gemini_client

        client = gemini_client()
    if verify is None:
        from grit.agents.weekly_evidence import verify_page

        verify = verify_page

    blockers: list[str] = []
    calls: list[agents.CallRecord] = []
    notes: dict[str, list[str]] = {}
    withheld: list[dict[str, Any]] = []
    repeats: list[dict[str, Any]] = []

    def record(call: agents.CallRecord) -> None:
        calls.append(call)
        pool.log(actor_type="AGENT", actor=call.agent, status=call.status, run_id=scan_id, version="adverse-1", model=model, subject=call.key,
                 detail={"attempts": call.attempts, "violations": call.violations[:20]})

    # Screener
    kept: dict[str, list[Candidate]] = {"A": [], "B": [], "C": []}
    screened = 0
    for section in ("A", "B", "C"):
        pool_of = candidates[section]
        keyed = {f"{section}{n}": c for n, c in enumerate(pool_of, start=1)}
        allowed_a = {k for k, c in keyed.items() if c.rung in {DIRECT, COUNTERPARTY}}
        keys = list(keyed)
        for start in range(0, len(keys), agents.SCREEN_BATCH):
            batch_keys = keys[start:start + agents.SCREEN_BATCH]
            decisions, call = agents.screen_batch(client, model, [keyed[k].compact(k) for k in batch_keys], allowed_a=allowed_a, thinking=thinking)
            record(call)
            screened += len(batch_keys)
            if decisions is None:
                blockers.append(f"The Screener failed on {len(batch_keys)} Section {section} candidates ({call.violations[-1] if call.violations else call.status}).")
                continue
            for key in batch_keys:
                chosen = decisions[key]["section"]
                if chosen != "NONE":
                    keyed[key].section = chosen
                    kept[chosen].append(keyed[key])
    counts["screened"] = screened
    counts.update({f"screened_in_{s}": len(v) for s, v in kept.items()})
    log(f"Screener kept {len(kept['A'])} A, {len(kept['B'])} B and {len(kept['C'])} C candidates.")

    # Group, verify, match earlier reports
    earlier = previous_items(pool, scan_id, run_date)
    cache_path = folder / "verify.json"
    folder.mkdir(parents=True, exist_ok=True)
    cache: dict[str, Any] = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
    anchors = anchor_names(graph, lob_profiles)
    jobs: list[dict[str, Any]] = []
    for section in ("A", "B", "C"):
        items = sorted(group(kept[section], section), key=lambda i: -i.lead.score)
        for over in items[ASSESS_CAPS[section]:]:
            withheld.append({"section": section, "headline": over.lead.title, "url": over.lead.url, "reason": f"Over the Section {section} assessment cap of {ASSESS_CAPS[section]} items."})
        for item in items[:ASSESS_CAPS[section]]:
            sources, refs, failures = verified_sources(item, verify, cache, band_rank)
            if not sources:
                withheld.append({"section": section, "headline": item.lead.title, "url": item.lead.url,
                                 "reason": "No source page could be opened, so nothing was verified: " + "; ".join(failures[:3])})
                continue
            jobs.append({"section": section, "item": item, "sources": sources, "refs": refs, "failures": failures, "previous": match_previous(item, earlier)})
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    counts["verified_pages"] = sum(len(job["refs"]) for job in jobs)
    counts["unverified_pages"] = sum(len(job["failures"]) for job in jobs)
    log(f"Assessing {len(jobs)} items with {counts['verified_pages']} verified pages.")

    period = {"start": window["start"].isoformat(), "end": window["end"].isoformat()}
    footprint = sorted(node.name for node in graph.of_class("Place") if graph.entities_in_place(node.id))

    def assess(job: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, agents.CallRecord]:
        item: Item = job["item"]
        stated = any(ref.get("published") for ref in job["refs"])
        previous = job["previous"]
        earlier_view = ({"run_date": previous["run_date"], "headline": previous["headline"], "evidence": previous["evidence"]} if previous else None)
        task = {"report_date": run_date.isoformat(), "reporting_period": period, "link": item.lead.rung or "none",
                "register": register_facts(item, graph, registers), "ste_context": {"countries_with_ste_entities": footprint},
                "lexicon": sorted({hit.category for m in item.members for hit in m.hits if hit.where != "BODY"})}
        draft, call = agents.assess_item(client, model, job["section"], task, job["sources"], stated_publication=stated, previous=earlier_view,
                                         anchors=anchors, key=item.lead.signal_id, thinking=thinking)
        return job, draft, call

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        results = list(executor.map(assess, jobs))

    included: dict[str, list[dict[str, Any]]] = {"A": [], "B": [], "C": []}
    for job, draft, call in results:
        record(call)
        item: Item = job["item"]
        section = job["section"]
        if draft is None:
            reason = "The model call failed." if call.status == "LLM_ERROR" else "The draft failed the checks twice: " + "; ".join(call.violations[-3:])
            withheld.append({"section": section, "headline": item.lead.title, "url": item.lead.url, "reason": reason})
            continue
        if draft.get("include") is False:
            withheld.append({"section": section, "headline": item.lead.title, "url": item.lead.url, "reason": f"Assessor: {draft.get('exclusion_reason')}"})
            continue
        previous = job["previous"]
        if previous and draft.get("update_status") == "NO_MATERIAL_UPDATE":
            repeats.append({"section": section, "headline": str(draft.get("headline") or item.lead.title), "previous_date": previous["run_date"],
                            "signal_ids": [m.signal_id for m in item.members]})
            continue
        refs = job["refs"]
        published = next((ref["published"] for ref in refs if ref.get("published")), None)
        if published:
            publication, display = published, None
        elif draft.get("publication_date") not in (None, "not stated"):
            publication, display = draft["publication_date"], None
        else:
            publication, display = None, f"not stated by the source; first seen by GRIT-Scan on {render.long_date(min(r['first_seen'] for r in refs))}"
        event_date = draft.get("event_date")
        update = previous is not None and draft.get("update_status") == "MATERIAL_UPDATE"
        if publication and not in_window(publication, window["start"], window["end"]) and not update and not (
                isinstance(event_date, str) and in_window(event_date, window["start"], window["end"])):
            withheld.append({"section": section, "headline": str(draft.get("headline")), "url": item.lead.url,
                             "reason": f"OUT_OF_PERIOD: published {publication}, outside the reporting period, with no new development in it."})
            continue
        corro = corroboration(item, lambda url: registrable(host_of(url)))
        facts = {"classes": [graph.nodes[n].cls for n in item.node_ids if n in graph.nodes], "kinds": [graph.nodes[n].kind for n in item.node_ids if n in graph.nodes],
                 "ste_named": ste_named(job["sources"], index), "publication_stated": bool(publication), "corroborated": corro["corroborated"],
                 "business_areas": list(dict.fromkeys(a for n in item.node_ids for a in business_areas(graph, n)))}
        decided, rule_notes = (checks.decide_a(draft, facts) if section == "A" else checks.decide_b(draft, facts) if section == "B" else checks.decide_c(draft, facts))
        decided.update({"section": section, "sources": refs, "publication_date": publication, "publication_display": display,
                        "link": item.lead.rung or "none", "signal_ids": [m.signal_id for m in item.members],
                        "event_ids": sorted({m.event_id for m in item.members if m.event_id}), "entity_ids": item.node_ids,
                        "outlets": corro["outlets"], "previous": ({"run_date": previous["run_date"], "item_id": previous["item_id"]} if update else None),
                        "_item": item})
        decided["item_id"] = f"{section}?{len(included[section]) + 1}"
        if rule_notes:
            notes[decided["item_id"]] = rule_notes
        included[section].append(decided)

    # Same-event items that grouping could not join before assessment (different names or titles) become one item.
    for section in ("A", "B", "C"):
        kept_items, merged = checks.merge_duplicates(included[section])
        for entry, home in merged:
            notes.setdefault(home["item_id"], []).append(f"Merged the same event, '{entry['headline']}', into this item with its sources.")
        included[section] = kept_items

    # Corroboration searches for Critical and High items with one outlet, within the remaining query limit.
    sent = len(pool.rows("SELECT query_id FROM searches WHERE run_id = ?", (scan_id,)))
    budget = max(0, limit - sent) if not no_search else 0
    search_result["corroboration"] = 0
    for section in ("A", "B"):
        for entry in included[section]:
            if budget <= 0 or entry["severity"] not in {"Critical", "High"} or entry.get("corroborated"):
                continue
            item = entry["_item"]
            name = next((graph.nodes[n].name for n in item.node_ids if n in graph.nodes), "")
            words = [w for w in item.lead.title.split() if len(w) > 3][:6]
            spec = PlannedQuery(f"ADV-CORR-{item.lead.signal_id[4:12]}", f"{name} {' '.join(words)}".strip(), "CORROBORATION")
            result = search(pool, scan_id, [spec], window, folder, limit=1, getter=getter, usage_path=usage_path, sleep=sleep, log=log)
            budget -= result["queries_sent"]
            search_result["corroboration"] += result["queries_sent"]
            found = pool.rows("SELECT s.* FROM sightings g JOIN signals s ON s.signal_id = g.signal_id WHERE g.run_id = ? AND g.query_id = ?", (scan_id, spec.query_id))
            outlets = set(entry["outlets"])
            for row in found:
                host = registrable(host_of(row["url"]))
                if host in outlets or band_of(row["url"], row.get("publisher")) in {"T4", "CONTESTED"} or similar(row["title"], item.lead.title) < 0.3:
                    continue
                check = cache.get(row["url"]) or dict(verify(row["url"]))
                cache[row["url"]] = check
                if check.get("status") == "SUCCEEDED" and name and name.casefold() in str(check.get("text") or "").casefold():
                    entry["corroborated"] = True
                    entry["sources"].append({"source_id": f"S{len(entry['sources']) + 1}", "outlet": str(row.get("publisher") or host), "url": row["url"],
                                             "published": (row.get("published_at") or "")[:10] or None, "first_seen": str(row["first_seen_at"])[:10],
                                             "signal_id": row["signal_id"], "band": band_of(row["url"], row.get("publisher"))})
                    break
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    # Critic
    everything = [entry for section in ("A", "B", "C") for entry in included[section]]
    critic_ok = True
    if everything:
        verdicts, same_event, critic_calls = agents.critique(client, model, [agents.critic_view(e) for e in everything], period, thinking=thinking)
        for call in critic_calls:
            record(call)
        if verdicts is None:
            critic_ok = False
            blockers.append("The Critic did not complete its review.")
            verdicts = {}
        for section in ("A", "B", "C"):
            kept_items = []
            for entry in included[section]:
                changed, reason = checks.apply_critic(entry, verdicts.get(entry["item_id"], {}))
                if changed is None:
                    withheld.append({"section": section, "headline": entry["headline"], "url": entry["sources"][0]["url"], "reason": f"Critic withdrew it: {reason}"})
                    continue
                if reason:
                    notes.setdefault(entry["item_id"], []).append(f"Critic downgraded it: {reason}")
                kept_items.append(changed)
            kept_items, merged = checks.merge_groups(kept_items, same_event)
            for entry, home in merged:
                notes.setdefault(home["item_id"], []).append(f"The Critic found '{entry['headline']}' reports the same event; merged here with its sources.")
            included[section] = kept_items

    # Order and number
    final: dict[str, list[dict[str, Any]]] = {}
    for section in ("A", "B", "C"):
        ordered = sorted(included[section], key=lambda e: (e.get("category") or "", checks.SEVERITIES.index(e["severity"]), e["headline"]))
        for number, entry in enumerate(ordered, start=1):
            old = entry["item_id"]
            entry["item_id"] = f"{section}-{number}"
            if old in notes:
                notes[entry["item_id"]] = notes.pop(old)
            entry.pop("_item", None)
        final[section] = ordered

    high = [e for s in ("A", "B") for e in final[s] if e["severity"] in {"Critical", "High"}]
    no_date = sum(1 for s in final.values() for e in s if not e.get("publication_date"))
    reported = sum(len(v) for v in final.values())
    validation = [
        f"Every reported item cites only pages that were opened and returned their text; every fact is quoted verbatim from those pages ({reported} items, "
        f"{sum(len(e['sources']) for s in final.values() for e in s)} pages).",
        f"Numbers in the narrative appear in the cited pages; outlet names, dates and links come from the record of each page, not from the model.",
        f"Event and publication dates are shown for every item; {no_date} publication date(s) not stated by the source and labelled.",
        f"Critical and High items corroborated by an official source or a second independent outlet: {sum(1 for e in high if e.get('corroborated'))} of {len(high)}.",
        f"Earlier reports checked: {len(earlier)} items from the last {PREVIOUS_DAYS} days; {len(repeats)} repeat(s) with no material update left out.",
        f"Withheld by the tests, the rules or the Critic: {len(withheld)} (listed in the reviewer worksheet).",
        "Searches: " + (f"{search_result.get('queries_sent', 0)} sent this run, {search_result.get('corroboration', 0)} for corroboration"
                        + (f"; stopped: {search_result['stopped']}" if search_result.get("stopped") else "") + "."),
    ]
    if not critic_ok:
        validation.append("The independent Critic did not complete its review.")
    validation.append("Email accuracy check: recipient, subject line, greeting and closing confirmed.")
    report: dict[str, Any] = {
        "scan_id": scan_id, "run_date": run_date.isoformat(), "status": "READY" if not blockers else "INCOMPLETE", "blockers": blockers,
        "window": {"start": window["start"].isoformat(), "end": window["end"].isoformat(), "previous": window["previous"]},
        "counts": counts, "search": search_result, "items": final, "repeats": repeats, "withheld": withheld, "notes": notes,
        "overall": checks.overall(final), "attention": checks.attention(final), "validation": validation,
        "calls": [asdict(call) for call in calls], "model": model,
    }
    problems = render.email_problems(render.compose(report), run_date)
    if problems:
        report["blockers"] = blockers = blockers + [f"Email accuracy check failed: {p}" for p in problems]
        report["status"] = "INCOMPLETE"
        report["validation"][-1] = "Email accuracy check failed; see the reviewer worksheet."
    paths = render.write(folder, report)
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _persist(pool, scan_id, report, paths)
    pool.finish_run(scan_id, "COMPLETE" if report["status"] == "READY" else "FAILED", notes="; ".join(blockers) or None)
    report["paths"] = {key: str(value) for key, value in paths.items()}
    return report


def _persist(pool: Pool, scan_id: str, report: Mapping[str, Any], paths: Mapping[str, Path]) -> None:
    now = utc_now()
    with pool.transaction() as conn:
        conn.execute("DELETE FROM adverse_items WHERE scan_id = ?", (scan_id,))
    for section, entries in report["items"].items():
        for entry in entries:
            pool.insert("adverse_items", {"scan_id": scan_id, "item_id": entry["item_id"], "section": section, "status": "INCLUDED",
                                          "entity_ids_json": entry.get("entity_ids") or [], "signal_ids_json": entry.get("signal_ids") or [],
                                          "event_ids_json": entry.get("event_ids") or [], "headline": entry["headline"], "severity": entry["severity"],
                                          "item_json": {key: value for key, value in entry.items() if key != "_item"}, "created_at": now})
    for number, entry in enumerate(report["withheld"], start=1):
        pool.insert("adverse_items", {"scan_id": scan_id, "item_id": f"W-{number}", "section": entry.get("section"), "status": "WITHHELD",
                                      "headline": entry["headline"], "reason": entry["reason"], "created_at": now})
    for number, entry in enumerate(report["repeats"], start=1):
        pool.insert("adverse_items", {"scan_id": scan_id, "item_id": f"R-{number}", "section": entry.get("section"), "status": "REPEAT",
                                      "signal_ids_json": entry.get("signal_ids") or [], "headline": entry["headline"], "created_at": now})
    with pool.transaction() as conn:
        conn.execute("UPDATE adverse_scans SET status = ?, blockers_json = ?, report_path = ?, finished_at = ? WHERE scan_id = ?",
                     (report["status"], json.dumps(list(report["blockers"]), ensure_ascii=False), str(paths["email"]), now, scan_id))


def main(argv: list[str] | None = None) -> int:
    from grit.settings import load_local_env

    parser = argparse.ArgumentParser(prog="grit adverse", description="Write the weekly Adverse News & Lessons Learnt Scan as an email (spec 46). Nothing is sent.")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today(), help="Report date, YYYY-MM-DD. Default today.")
    parser.add_argument("--since", type=date.fromisoformat, help="Start of the reporting period. Default: the end of the previous READY scan, else seven days.")
    parser.add_argument("--limit-queries", type=int, help="At most N Brave queries, including corroboration. Default: the scan plan's limit, 40.")
    parser.add_argument("--no-search", action="store_true", help="Use the pool only; send no Brave query.")
    parser.add_argument("--dry-run", action="store_true", help="Print the period, the planned queries and the candidate counts. No query, no model call.")
    parser.add_argument("--workers", type=int, default=4, help="Assessor calls in flight at once. Default 4.")
    parser.add_argument("--model", help="Gemini model. Default: GEMINI_MODEL, else gemini-2.5-flash.")
    parser.add_argument("--quiet", action="store_true", help="Do not print the email at the end.")
    args = parser.parse_args(argv)
    load_local_env()
    import os

    pool = Pool()
    try:
        report = run_scan(args.date, pool=pool, model=args.model or os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash", since=args.since,
                          limit_queries=args.limit_queries, no_search=args.no_search, dry_run=args.dry_run, workers=args.workers)
    finally:
        pool.close()
    if report["status"] == "DRY_RUN":
        return 0
    print(f"\nStatus: {report['status']}")
    for blocker in report["blockers"]:
        print(f"  BLOCKER: {blocker}")
    for key, path in report["paths"].items():
        print(f"  {key:9} {path}")
    if not args.quiet:
        print("\n" + "=" * 100 + "\n")
        print(Path(report["paths"]["email"]).read_text(encoding="utf-8"))
    return 0 if report["status"] == "READY" else 1
