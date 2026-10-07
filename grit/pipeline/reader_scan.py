"""The reader scan: one reader pack, one week. Every stage reads and writes the pool, so a stage can be rerun alone.

  plan      the reader pack's queries (conditions, projects, suppliers, customers, hazards, watchpoints).
  acquire   Researcher: Brave News, plus up to two Currents pulls. A query already answered in this run is not sent again.
  embed     Gemini embeddings into the pool, through the shared vector cache.
  assess    deterministic: document type, link ladder, reader-pack hits, keep rule, priority, risk cascade, harm scorer,
            relevance labels, and the RED rule. No model.
  agents    Assessor drafts each standing condition, Critic argues, Reporter writes item entries and the bottom line.
            Checks decide what is valid. Watchpoints are updated by rule.
  findings  the six-question finding loop for the strongest register-linked signals.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
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
from grit.ontology.reader_index import CONDITION, CUSTOMER, HAZARD, PROJECT, SITE, SUPPLIER, WATCHPOINT, ReaderIndex
from grit.ontology.reader_plan import reader_queries, target_of
from grit.ontology.risk_cascade import load_risk_cascade
from grit.pipeline.harm import harm_verdict, opportunity_status, score_harm
from grit.pool import Pool
from grit.settings import CONFIG_DIR, OUTPUT_DIR

EXPOSURE_TYPES = (PROJECT, SUPPLIER, CUSTOMER, SITE)
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
        raise ValueError(f"Pack {run.pack} has no scan plan. Add the pack's *-Scan-Plan.json register.")
    seed_watchpoints(pool, pack)
    return reader_queries(pack.scan_plan, pool.watchpoints(run.pack), pool.management_requests(active_only=True))


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
) -> dict[str, Any]:
    """Researcher. Sends each query once per run, writes every result into the pool as it returns, and logs every search."""
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

    def on_search(entry: dict[str, Any]) -> None:
        with (run.folder / "searches.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        candidates, hits = candidates_from_searches([entry], fetched_at=entry["fetched_at"])
        spec = by_id[entry["query_id"]]
        for candidate in candidates:
            before = pool.one("SELECT 1 FROM signals WHERE url = ?", (candidate.url,))
            signal_id = pool.add_signal(
                url=candidate.url, title=candidate.title, text=candidate.text, publisher=candidate.publisher, published_at=candidate.published_at,
                lane="BRAVE", content_hash=candidate.content_hash, seen_at=entry["fetched_at"],
            )
            found["signals_new"] += 0 if before else 1
            for hit in hits.get(candidate.url, []):
                pool.add_sighting(signal_id, run.run_id, query_id=spec.query_id, query=spec.query, lane=spec.lane, target_id=target_of(spec), rank=hit.get("rank"))
        found["queries_sent"] += 1
        found["results"] += len(entry.get("results") or [])
        log(f"  {spec.lane:10} {len(entry.get('results') or []):>3} results  {spec.query}")

    stopped = None
    if todo:
        options: dict[str, Any] = {"usage_path": usage_path} if usage_path else {}
        if sleep is not None:
            options["sleep"] = sleep
        result = collect_plan(todo, fetched_at=fetched_at, freshness="pw", country="ALL", getter=getter, on_search=on_search, **options)
        stopped = result.stopped
    if currents_queries:
        found["currents"] = _currents(pool, run, currents_queries[:2], currents_getter, fetched_at)
    found["stopped"] = stopped
    pool.log(actor_type="AGENT", actor="Researcher", status="SUCCEEDED" if not stopped else stopped, run_id=run.run_id, version="brave-news-1", detail=found)
    return found


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
    business = _load_pack("urs")
    graph = build_graph(business, reviewed_aliases=_aliases())
    index = LinkIndex(graph, business.lob_profiles)
    seed_watchpoints(pool, reader)
    readers = ReaderIndex(reader.exposure, reader.scan_plan, reader.baseline, pool.watchpoints(run.pack))
    cascade = load_risk_cascade()
    patterns = red_patterns if red_patterns is not None else json.loads((CONFIG_DIR / "red-patterns.json").read_text(encoding="utf-8"))
    conditions = {condition["id"]: condition for condition in reader.baseline.get("conditions") or []}
    registers = [reader.source_register, business.source_register]
    window = int((reader.scan_plan or {}).get("windowDays") or 8)

    signals = pool.signals_for_run(run.run_id)
    records: list[dict[str, Any]] = []
    for signal in signals:
        signal = {**signal, "text": without_navigation(signal["text"])}
        candidate = {"title": signal["title"], "text": signal["text"], "publisher": signal["publisher"], "url": signal["url"], "published_at": signal["published_at"]}
        doc = classify(candidate)
        pool.set_doc_type(signal["signal_id"], doc.doc_type)
        records.append({
            **signal, "doc_type": doc, "link": link_candidate(candidate, index, business.lob_profiles), "hits": readers.hits(signal["title"], signal["text"]), "scale": scale_cues(candidate),
        })
    decisions = filter_reader(records, run_date=run.run_date, vectors=run_vectors(pool, run), config=ReaderFilterConfig(window_days=window))
    sightings = defaultdict(set)
    for row in pool.sightings(run.run_id):
        if row.get("target_id"):
            sightings[row["signal_id"]].add(row["target_id"])

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
            harm = score_harm_fn(record, run_date=run.run_date, credibility=credibility, lob=link.canonical_lob)
            risks = cascade.hits(record["title"], record["text"], [theme.theme_id for theme in link.themes if theme.strong])
            entity_themes = [{"themeId": hit.risk_id, "themeName": hit.name, "tier": hit.tier} for hit in risks if hit.level == "ENTITY"]
            condition = conditions.get(condition_ids[0]) if condition_ids else None
            high_rated = any(item.get("credibilityRating") == "HIGH" for item in credibility)
            verdict = harm_verdict(harm)
            red_result = red_check({**record, "rung": decision["rung"], "harm_verdict": verdict}, pack=run.pack, patterns=patterns, corroboration=decision["corroboration"], high_rated_source=high_rated)
            red += int(red_result.red)
            row |= {
                "risk_cascade_json": [hit.as_record() for hit in risks], "harm_json": harm, "harm_verdict": verdict, "opportunity_cost": opportunity_status(harm),
                "trend": "NO_MATERIAL_CHANGE", "risk_level": classify_wild_scan_risk_level(entity_themes),
                "relevance_basis": compose_wild_scan_basis(condition, publisher=record["publisher"]) if condition else _basis(decision, link, exposure),
                "evidence_confidence": evidence_confidence(credibility)[0], "red_rule_json": red_result.as_record(), "red": int(red_result.red),
            }
        pool.save_assessment(row)
    result = {"signals": len(records), "kept": kept, "red": red, "dropped": _count(decisions)}
    pool.log(actor_type="FUNCTION", actor="ReaderAssess", status="SUCCEEDED", run_id=run.run_id, version="reader-assess-1", detail=result)
    return result


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


def condition_sources(rows: Sequence[Mapping[str, Any]], condition_id: str) -> list[Mapping[str, Any]]:
    """Kept signals that bear on a condition: a strong keyword hit, or found by one of the condition's own queries."""
    chosen = [row for row in rows if condition_id in (row.get("condition_ids") or []) or condition_id in (row["link"].get("sighting_targets") or [])]
    return _recent_first(chosen)[:MAX_CONDITION_SOURCES]


def _prior(pool: Pool, run: ReaderRun) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]]]:
    previous = pool.latest_run(run.pack, before=run.run_date.isoformat(), kind="READER_SCAN")
    if previous is None:
        return None, {}
    return previous, {row["condition_id"]: row for row in pool.conditions(previous["run_id"])}


def run_agents(pool: Pool, run: ReaderRun, *, client: Any, model: str, log: Callable[[str], None] = print) -> dict[str, Any]:
    from grit.agents import reader as agents
    from grit.core.conditions import import_scan, rank, revise

    pack = _load_pack(run.pack)
    rows = _kept(pool, run)
    previous_run, priors = _prior(pool, run)
    with pool.transaction() as conn:
        conn.execute("DELETE FROM qa_log WHERE run_id = ?", (run.run_id,))  # derived per run; a rerun of the agents writes it again
    registers = [pack.source_register, _load_pack("urs").source_register]
    by_signal = {row["signal_id"]: row for row in rows}

    def write_log(entry: agents.CallLog) -> None:
        record = entry.as_record()
        pool.log(actor_type="AGENT", run_id=run.run_id, **record)

    # Assessor: one draft per standing condition.
    drafts: dict[str, dict[str, Any]] = {}
    tables: dict[str, tuple[dict[str, dict[str, Any]], dict[str, str]]] = {}
    for condition in pack.baseline.get("conditions") or []:
        sources = condition_sources(rows, condition["id"])
        table, back = agents.source_table(sources)
        tables[condition["id"]] = (table, back)
        prior = priors.get(condition["id"])
        prior_view = {"trend": prior.get("trend_label"), "summary": prior.get("summary"), "run_date": previous_run["run_date"]} if prior else None
        draft, call = agents.assess_condition(client, model, condition=condition, prior=prior_view, sources=table)
        write_log(call)
        drafts[condition["id"]] = draft
        log(f"  Assessor {condition['id']} {draft['status']:12} {draft.get('movement') or '-':20} sources {len(table)}")

    # Critic: one pass over every validated draft, against last week's text and the same sources.
    combined, combined_back = _combined_table(tables, drafts)
    critic_view = [
        {"item_id": key, "name": next(c["name"] for c in pack.baseline["conditions"] if c["id"] == key), "movement": draft.get("movement"), "summary": _renumber(draft.get("summary") or "", tables[key][1], combined_back)}
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
        condition = next(c for c in pack.baseline["conditions"] if c["id"] == key)
        prior = priors.get(key)
        prior_view = {"trend": prior.get("trend_label"), "summary": prior.get("summary"), "run_date": previous_run["run_date"]} if prior else None
        refined, call = agents.assess_condition(client, model, condition=condition, prior=prior_view, sources=table, issues=[fixed_issue], previous=drafts[key])
        write_log(call)
        before = drafts[key].get("summary")
        if refined["status"] in {"VALIDATED", "NO_EVIDENCE"}:
            drafts[key] = refined
            pool.add_qa(run.run_id, item=f"{key}: {condition['name']}", prior=str(issue.get("problem") or before), correction=agents.strip_marks(fixed_issue["correction"]), source_ids=[str(source_signal)], origin="CRITIC")
        else:
            pool.add_qa(run.run_id, item=f"{key}: {condition['name']}", prior=str(issue.get("problem")), correction="Raised by the Critic. The redraft failed its checks, so the earlier draft stands.", source_ids=[str(source_signal)], origin="CRITIC")

    # Persist the conditions, then run the governed weekly cascade over them for themes, descent and priority.
    scan: list[dict[str, Any]] = []
    for condition in pack.baseline.get("conditions") or []:
        key = condition["id"]
        draft = drafts[key]
        table, back = tables[key]
        cited = [back[item] for item in draft.get("source_ids") or [] if item in back]
        credibility = [_credibility(by_signal[sid]["url"], by_signal[sid]["publisher"], registers) for sid in cited if sid in by_signal]
        level, _reason = evidence_confidence(credibility)
        summary = _cite(draft.get("summary") or "", back) if draft.get("summary") else None
        status = draft["status"]
        if status == "WITHHELD":
            pool.add_qa(run.run_id, item=f"{key}: {condition['name']}", prior=None, correction="Withheld: the Assessor's draft failed its checks twice. " + "; ".join(draft["violations"][:2]), origin="CHECK")
        pool.save_condition({
            "run_id": run.run_id, "pack": run.pack, "condition_id": key, "condition_name": condition["name"], "movement": draft.get("movement"),
            "trend_label": trend_label(draft.get("movement")) if status != "WITHHELD" else "Not established", "confidence": level,
            "summary": summary, "source_ids_json": cited, "status": status, "violations_json": draft.get("violations") or [],
        })
        if status == "VALIDATED":
            scan.append({
                "conditionHint": key, "movement": draft["movement"], "evidenceConfidence": level or "LOW", "summary": agents.strip_marks(draft["summary"]),
                "sources": [{"url": by_signal[sid]["url"]} for sid in cited if sid in by_signal],
            })
    revision = revise(import_scan(json.dumps(scan), pack.baseline, revision_id=run.run_id, revision_date=run.run_date.isoformat()), pack)
    ranking = {item["conditionId"]: item for item in rank(revision, pack)["rankedItems"]}
    for item in revision["revisions"]:
        pool.conn.execute("UPDATE condition_assessments SET ranking_json = ? WHERE run_id = ? AND condition_id = ?", (json.dumps({**item, **ranking.get(item["conditionId"], {})}, default=str), run.run_id, item["conditionId"]))
    pool.conn.commit()

    items = _item_checks(pool, run, pack, rows, client=client, model=model, write_log=write_log, log=log)
    watch = update_watchpoints(pool, run, rows)
    bottom = _bottom_line(pool, run, client=client, model=model, write_log=write_log)
    return {"conditions": {key: draft["status"] for key, draft in drafts.items()}, "critic_accepted": len(accepted), "items": items, "watchpoints": watch, "bottom_line": bottom["status"]}


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


def _item_rows(pack: OntologyPack) -> list[tuple[str, str, str]]:
    exposure = pack.exposure
    plan = pack.scan_plan or {}
    found = [(PROJECT, row["projectId"], row["projectName"]) for row in exposure.get("projects") or []]
    found += [(SUPPLIER, row["supplierId"], row["supplierName"]) for row in exposure.get("suppliers") or []]
    found += [(CUSTOMER, row["customerId"], row["customerName"]) for row in exposure.get("customers") or []]
    found += [(CUSTOMER, row["itemId"], row.get("name") or row["itemId"]) for row in plan.get("customers") or [] if row.get("aliases")]
    found += [(HAZARD, row["itemId"], row.get("name") or row["itemId"]) for row in plan.get("hazards") or []]
    return found


def _item_checks(pool: Pool, run: ReaderRun, pack: OntologyPack, rows: Sequence[Mapping[str, Any]], *, client: Any, model: str, write_log: Callable[[Any], None], log: Callable[[str], None]) -> dict[str, int]:
    """Every named item gets a row. Strong hits go to the Reporter. Passing mentions are listed by rule. None is NOTHING_FOUND."""
    from grit.agents import reader as agents

    strong_by_item: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    weak_by_item: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        for hit in row.get("exposure") or []:
            (strong_by_item if hit.get("strong") else weak_by_item)[hit["item_id"]].append(row)
        for hit in row["link"].get("hazards") or []:
            strong_by_item[hit["item_id"]].append(row)
    sources: list[Mapping[str, Any]] = []
    index: dict[str, str] = {}
    requests: list[dict[str, Any]] = []
    for item_type, item_id, name in _item_rows(pack):
        chosen = _recent_first(strong_by_item.get(item_id, []))[:MAX_ITEM_SOURCES]
        ids = []
        for row in chosen:
            if row["signal_id"] not in index:
                sources.append(row)
                index[row["signal_id"]] = f"S{len(sources)}"
            ids.append(index[row["signal_id"]])
        if ids:
            requests.append({"item_id": item_id, "item_type": item_type, "name": name, "source_ids": ids})
    table, back = agents.source_table(sources)
    results, call = agents.report_items(client, model, items=requests, sources=table)
    write_log(call)
    counts: dict[str, int] = defaultdict(int)
    for item_type, item_id, name in _item_rows(pack):
        result = results.get(item_id)
        weak = weak_by_item.get(item_id, [])
        if result and result["status"] == "VALIDATED":
            status, summary, cited = "FOUND", _cite(result["summary"], back), [back[s] for s in result["source_ids"] if s in back]
        elif result:
            status, summary, cited = "WITHHELD", None, []
            pool.add_qa(run.run_id, item=f"{item_type.title()}: {name}", correction="Withheld: the Reporter's entry failed its checks. " + "; ".join(result["violations"][:2]), origin="CHECK")
        elif weak:
            titles = "; ".join(f"\u201c{row['title']}\u201d [{row['signal_id']}]" for row in _recent_first(weak)[:2])
            status, summary, cited = "NOTHING_FOUND", f"Passing mention only, with nothing specific to URS Taiwan: {titles}.", [row["signal_id"] for row in weak[:2]]
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


def bottom_line_facts(pool: Pool, run: ReaderRun) -> dict[str, Any]:
    from grit.agents.checks import strip_marks

    return {
        "reader": "Group Head, Risk & Assurance (Wilson); the brief is also read by the URS R&A business partner and the URS Taiwan head",
        "conditions": [{"condition": row["condition_name"], "trend": row["trend_label"], "confidence": row["confidence"], "summary": strip_marks(row["summary"] or "") or None} for row in pool.conditions(run.run_id)],
        "named_items": [{"type": row["item_type"], "name": row["item_name"], "status": row["status"], "summary": strip_marks(row["summary"] or "") or None} for row in pool.exposure_checks(run.run_id) if row["status"] != "NOTHING_FOUND" or row["summary"]],
        "named_items_with_nothing_found": [row["item_name"] for row in pool.exposure_checks(run.run_id) if row["status"] == "NOTHING_FOUND" and not row["summary"]],
        "watchpoints": [
            {"title": row["title"], "status": row["status"], "detail": row["detail"], "quiet_cycles": row["cycles_without_update"],
             "this_week": (pool.one("SELECT note FROM watchpoint_updates WHERE watchpoint_id = ? AND run_id = ?", (row["watchpoint_id"], run.run_id)) or {}).get("note")}
            for row in pool.watchpoints(run.pack)
        ],
        "red_alert_drafts": [row["title"] for row in pool.assessments(run.run_id, kept_only=True) if row["red"]],
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

    business = _load_pack("urs")
    graph = build_graph(business, reviewed_aliases=_aliases())
    index = LinkIndex(graph, business.lob_profiles)
    rows = [row for row in _kept(pool, run) if row["link"].get("rung") and (row.get("tier") or 9) <= max_tier][:top]
    vectors_by_id = run_vectors(pool, run)
    every = pool.signals_for_run(run.run_id)
    by_url = {signal["url"]: signal for signal in every}
    vectors = {signal["url"]: vectors_by_id[signal["signal_id"]] for signal in every if signal["signal_id"] in vectors_by_id}
    pool_records = [{"url": s["url"], "title": s["title"], "text": s["text"], "publisher": s["publisher"], "published_at": s["published_at"]} for s in every]
    tool = FollowUpTool(search=search, embed=None, index=index, pool=pool_records, vectors=vectors, same_event=0.90, budget=top)
    engine = FindingLoop(client, model, graph=graph, index=index, themes=business.themes["themes"], pool=pool_records, vectors=vectors, tool=tool, config=LoopConfig())
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
