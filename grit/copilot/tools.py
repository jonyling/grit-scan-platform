"""The Co-Pilot's tools (spec 40, section 4). Each reads one store and registers what it returns as a citable source.

The model sees sources by short id and never a URL:

| Prefix | Store | Badge |
|---|---|---|
| S | a signal (one event, with all its outlets) | outlet and date, linked |
| B | a brief's state: a standing item, a bottom line, or a watchpoint | pack brief and run date |
| R | a report passage from the library | report, edition and page |
| D | a State of the Situation section (a draft until Wilson signs it off) | the document |
| G | a register row | the pack's register |
| W | a web search result (unvetted) | "Web, unvetted", outlet and date, linked |

No tool writes anything. Rendering adds outlets, dates and links from the pool, never from the model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from grit.copilot.search import LEAD, search
from grit.pool import Pool
from grit.settings import CONFIG_DIR, OUTPUT_DIR

SIG = re.compile(r"\[((?:SIG-[0-9a-f]{16})(?:\s*,\s*SIG-[0-9a-f]{16})*)\]")
INTERNAL_NOTE = "Internal report (Co-Confidential): quoted for ST Engineering readers only."
SOTS_NOTE = "State of the Situation draft: not yet signed off by Wilson."
MAX_STATE_ROWS = 16
MAX_HISTORY = 12
MAX_REGISTER = 15


@dataclass
class Source:
    source_id: str
    kind: str  # SIGNAL, STATE, REPORT, SOTS, REGISTER, WEB
    key: str
    text: str  # what numbers and quotes are checked against
    names: tuple[str, ...] = ()  # names a sentence may use to attribute a forecast to this source
    badge: str = ""
    url: str | None = None
    note: str | None = None  # an access note shown with the answer when this source is cited
    detail: dict[str, Any] = field(default_factory=dict)


class Sources:
    PREFIX = {"SIGNAL": "S", "STATE": "B", "REPORT": "R", "SOTS": "D", "REGISTER": "G", "WEB": "W"}

    def __init__(self) -> None:
        self.by_id: dict[str, Source] = {}
        self.by_key: dict[str, str] = {}

    def add(self, kind: str, key: str, **values: Any) -> Source:
        if key in self.by_key:
            return self.by_id[self.by_key[key]]
        prefix = self.PREFIX[kind]
        number = 1 + sum(1 for item in self.by_id if item.startswith(prefix) and item[len(prefix):].isdigit())
        source = Source(f"{prefix}{number}", kind, key, **values)
        self.by_id[source.source_id] = source
        self.by_key[key] = source.source_id
        return source

    def __contains__(self, source_id: str) -> bool:
        return source_id in self.by_id

    def __len__(self) -> int:
        return len(self.by_id)

    def get(self, source_id: str) -> Source | None:
        return self.by_id.get(source_id)


def _date(text: Any) -> str | None:
    return str(text)[:10] if text else None


class Toolbox:
    """The tools for one question, over one scope. Tool results go to the model as plain data with source ids."""

    def __init__(self, pool: Pool, scope: Any, *, embed: Callable[[str], Sequence[float]] | None = None, embedder: Any | None = None,
                 config_dir: Path = CONFIG_DIR, sots_dir: Path | None = None, library_dir: Path | None = None, web: Any | None = None) -> None:
        self.pool, self.scope, self.embed, self.embedder = pool, scope, embed, embedder
        self.web = web  # a grit.copilot.web.WebSearch, or None when the web is off
        self.config_dir = config_dir
        self.sots_dir = sots_dir or OUTPUT_DIR / "sots"
        self.library_dir = library_dir
        self.sources = Sources()
        self.calls: list[dict[str, Any]] = []
        self._packs: dict[str, Any] = {}

    # Registration -------------------------------------------------------------------------------------------------

    def _signal(self, row: Mapping[str, Any], outlets: Sequence[str] = (), dates: tuple[str | None, str | None] = (None, None)) -> Source:
        outlet = row.get("publisher") or "outlet not stated"
        when = _date(row.get("published_at")) or "date not stated"
        return self.sources.add(
            "SIGNAL", f"SIG:{row['signal_id']}", text=f"{row.get('title') or ''} {str(row.get('text') or '')[:LEAD]}",
            names=tuple(item for item in (outlet, *outlets) if item), badge=f"{outlet}, {when}", url=row.get("url"),
            detail={"signal_id": row["signal_id"], "title": row.get("title"), "outlet": outlet, "date": when},
        )

    def _cite_signals(self, text: str | None) -> str:
        """Brief text cites signals as [SIG-...]; the model sees them as S ids it may cite in turn."""
        if not text:
            return ""

        def swap(match: re.Match[str]) -> str:
            ids = []
            for signal_id in (part.strip() for part in match.group(1).split(",")):
                signal = self.pool.signal(signal_id)
                if signal:
                    ids.append(self._signal(signal).source_id)
            return f"[{', '.join(ids)}]" if ids else ""

        return SIG.sub(swap, text)

    def _state(self, key: str, text: str, badge: str, **detail: Any) -> Source:
        return self.sources.add("STATE", key, text=SIG.sub("", text), names=("GRIT-Scan", "the brief", "the assessment"), badge=badge, detail=detail)

    def _latest_run(self, pack: str, run_id: str | None = None) -> dict[str, Any] | None:
        if run_id:
            run = self.pool.run(run_id)
            return run if run and run["pack"] == pack and run["status"] == "COMPLETE" and run["kind"] != "DAILY" else None
        return self.pool.one("SELECT * FROM runs WHERE pack = ? AND status = 'COMPLETE' AND kind != 'DAILY' ORDER BY run_date DESC, started_at DESC LIMIT 1", (pack,))

    def _allowed(self, pack: str) -> str | None:
        if pack not in self.scope.packs:
            return f"{pack} is outside this reader's packs: {', '.join(self.scope.packs)}."
        return None

    # Tools --------------------------------------------------------------------------------------------------------

    def get_state(self, pack: str, run_id: str | None = None) -> dict[str, Any]:
        """The brief's state for a pack: bottom line, standing items with trend, summary, implication and options, and open
        watchpoints. Default: the pack's latest completed run."""
        refused = self._allowed(pack)
        if refused:
            return {"error": refused}
        run = self._latest_run(pack, run_id)
        if run is None:
            return {"error": f"No completed run for {pack}" + (f" with id {run_id}." if run_id else ".")}
        label = f"{self.scope.label(pack)} brief, {run['run_date']}"
        out: dict[str, Any] = {"pack": pack, "run_id": run["run_id"], "run_date": run["run_date"]}
        bottom = (run.get("config") or {}).get("bottom_line") or {}
        if bottom.get("status") == "VALIDATED" and bottom.get("bottom_line"):
            text = f"{bottom['bottom_line']} {bottom.get('escalation') or ''}".strip()
            out["bottom_line"] = {"id": self._state(f"BL:{run['run_id']}", text, label, kind="bottom line").source_id, "text": SIG.sub("", text)}
        items = []
        conditions = self.pool.conditions(run["run_id"])
        focus = self.scope.focus
        conditions.sort(key=lambda row: (row["condition_id"] != focus, row["movement"] != "ESCALATING", row["condition_id"]))
        for row in conditions[:MAX_STATE_ROWS]:
            summary = self._cite_signals(row.get("summary")) if row["status"] == "VALIDATED" else "No validated assessment this run."
            implication = self._cite_signals(row.get("implication"))
            options = [f"{option.get('option')} (owner: {option.get('owner') or 'not stated'}; trigger: {option.get('trigger') or 'not stated'})" for option in row.get("options") or []]
            text = " ".join(filter(None, [row["condition_name"], summary, implication, row.get("opportunity"), row.get("counter_indicator"), *options]))
            source = self._state(f"COND:{run['run_id']}:{row['condition_id']}", text, label, condition_id=row["condition_id"], name=row["condition_name"])
            items.append({
                "id": source.source_id, "item_id": row["condition_id"], "name": row["condition_name"], "trend": row.get("trend_label"),
                "confidence": row.get("confidence"), "summary": summary, "implication": implication or None, "opportunity": row.get("opportunity"),
                "counter_indicator": row.get("counter_indicator"), "options": options,
            })
        out["standing_items"] = items
        if len(conditions) > MAX_STATE_ROWS:
            out["more_items"] = len(conditions) - MAX_STATE_ROWS
        watch = []
        for row in self.pool.watchpoints(pack)[:8]:
            source = self._state(f"WP:{row['watchpoint_id']}", f"{row['title']} {row.get('detail') or ''}", f"{self.scope.label(pack)} watchpoint", watchpoint=row["watchpoint_id"])
            watch.append({"id": source.source_id, "title": row["title"], "detail": row.get("detail"), "quiet_cycles": row.get("cycles_without_update")})
        out["open_watchpoints"] = watch
        return out

    def condition_history(self, item_id: str, since: str | None = None) -> dict[str, Any]:
        """A standing condition's or theme's trend and summary at each completed run, oldest first, with turning points."""
        marks = ", ".join("?" for _ in self.scope.packs)
        params: list[Any] = [item_id, *self.scope.packs]
        where = ""
        if since:
            where = "AND r.run_date >= ?"
            params.append(since)
        rows = self.pool.rows(
            f"""SELECT c.*, r.run_date FROM condition_assessments c JOIN runs r ON r.run_id = c.run_id
                WHERE c.condition_id = ? AND c.pack IN ({marks}) AND r.status = 'COMPLETE' {where} ORDER BY r.run_date, r.started_at""",
            params,
        )
        if not rows:
            return {"error": f"No assessment of {item_id} in this reader's packs" + (f" since {since}." if since else ".")}
        rows = rows[-MAX_HISTORY:]
        out, previous = [], None
        for row in rows:
            summary = self._cite_signals(row.get("summary")) if row["status"] == "VALIDATED" else "No validated assessment this run."
            source = self._state(f"COND:{row['run_id']}:{item_id}", f"{row['condition_name']} {summary} {row.get('implication') or ''}",
                                 f"{self.scope.label(row['pack'])} brief, {row['run_date']}", condition_id=item_id, name=row["condition_name"])
            out.append({"id": source.source_id, "run_date": row["run_date"], "pack": row["pack"], "trend": row.get("trend_label"),
                        "turning_point": previous is not None and row.get("movement") != previous, "summary": summary})
            previous = row.get("movement")
        return {"item_id": item_id, "name": rows[-1]["condition_name"], "runs": out,
                "note": "Only one run so far: there is no trend history yet." if len(out) == 1 else None}

    def search_signals(self, query: str, include_dropped: bool = False) -> dict[str, Any]:
        """Events from the signal pool in this reader's scope, best first, one per story."""
        result = search(self.pool, self.scope, query, embed=self.embed, include_dropped=bool(include_dropped))
        events = []
        for event in result.events:
            row = event.row
            source = self._signal(row, event.outlets, event.dates)
            events.append({
                "id": source.source_id, "title": row.get("title"), "outlet": row.get("publisher"), "published": _date(row.get("published_at")),
                "outlets_carrying_it": len(event.outlets), "packs": row.get("packs"), "tier": row.get("tier"), "priority": (row.get("route") or {}).get("priority"),
                "link_rung": row.get("rung"), "standing_items": row.get("condition_ids") or [], "red": bool(row.get("red")),
                "kept": row.get("decision") == "KEEP", "text": str(row.get("text") or "")[:LEAD],
            })
        out = {
            "events": events, "signals_in_scope": result.in_scope, "runs_in_scope": result.runs, "events_matched": result.matched,
            "outside_aperture": result.outside_aperture, "newest_signal": self.newest_signal(),
        }
        if not events and self.web is not None:
            out["web_search_suggested"] = "No event in GRIT-Scan's records matches. web_search can look further."
        return out

    def web_search(self, query: str) -> dict[str, Any]:
        """Live web results, each an unvetted source."""
        from grit.copilot.web import UNVETTED

        if self.web is None:
            return {"error": "Web search is off for this conversation."}
        found = self.web(query)
        if found.get("error"):
            return found
        rows = []
        for item in found["results"]:
            outlet = item.get("publisher") or "outlet not stated"
            when = _date(item.get("published_at")) or "date not stated"
            source = self.sources.add(
                "WEB", f"WEB:{item['url']}", text=f"{item.get('title') or ''} {str(item.get('text') or '')[:LEAD]}", names=(outlet,),
                badge=f"Web, unvetted: {outlet}, {when}", url=item["url"], note=UNVETTED,
                detail={"title": item.get("title"), "outlet": outlet, "date": when, "text": str(item.get("text") or "")[:2000],
                        "publisher": item.get("publisher"), "published_at": item.get("published_at"), "packs": list(self.scope.packs)},
            )
            rows.append({"id": source.source_id, "title": item.get("title"), "outlet": outlet, "published": when, "text": str(item.get("text") or "")[:LEAD]})
        return {"results": rows, "engine": found.get("engine"), "unvetted": True, "searches_left_today": found.get("remaining_today"),
                "note": None if rows else "The web search found nothing." + (" " + "; ".join(found.get("tried") or []) if found.get("engine") == "NONE" else "")}

    def whats_new(self, since: str | None = None, pack: str | None = None) -> dict[str, Any]:
        """The daily digests since a date (default: the last 7 days): new tier 1 and 2 events, RED patterns, moved standing
        items and watchpoint updates, per pack and day."""
        from datetime import date as _date_type, timedelta

        from grit.daily.index import digests

        packs = [pack] if pack else list(self.scope.packs)
        refused = [item for item in packs if item not in self.scope.packs]
        if refused:
            return {"error": f"{', '.join(refused)} is outside this reader's packs."}
        try:
            start = _date_type.fromisoformat(since) if since else self.scope.today - timedelta(days=7)
        except ValueError:
            return {"error": "since is a date, YYYY-MM-DD."}
        rows = digests(self.pool, packs, start)
        if not rows:
            return {"days": [], "note": f"No daily digest since {start.isoformat()}. The daily lane may not have run."}
        out, budget = [], 24
        for digest in rows:
            events = []
            for item in digest.get("tier_1_2_events") or []:
                if budget <= 0:
                    break
                signal = self.pool.signal(item["signal_id"])
                if not signal:
                    continue
                source = self._signal(signal)
                events.append({"id": source.source_id, "title": item["title"], "outlet": item.get("outlet"), "published": item.get("published"), "tier": item.get("tier"),
                               "management_request": item.get("request"),
                               "outlets_carrying_it": item.get("outlets"), "standing_items": item.get("standing_items"), "red": item.get("red")})
                budget -= 1
            moved = []
            for item in digest.get("moved") or []:
                row = self.pool.one("SELECT c.*, r.run_date FROM condition_assessments c JOIN runs r ON r.run_id = c.run_id WHERE c.run_id = ? AND c.condition_id = ?", (item["run_id"], item["condition_id"]))
                if row:
                    summary = self._cite_signals(row.get("summary"))
                    source = self._state(f"COND:{row['run_id']}:{row['condition_id']}", f"{row['condition_name']} {summary}", f"{self.scope.label(row['pack'])} brief, {row['run_date']}",
                                         condition_id=row["condition_id"], name=row["condition_name"])
                    moved.append({"id": source.source_id, "name": row["condition_name"], "trend": row.get("trend_label"), "summary": summary})
            label = self.scope.label(digest["pack"])
            counts = (f"{label} daily digest for {digest['day']}: {digest.get('kept_new') or 0} newly kept pages; {len(digest.get('tier_1_2_events') or [])} new tier 1 or 2, RED or management-request events "
                      f"({digest.get('request_events') or 0} matching a management request); "
                      f"{digest.get('red_patterns') or 0} RED patterns; {len(digest.get('red_drafts') or [])} RED drafts; {len(digest.get('moved') or [])} standing items moved; "
                      f"{len(digest.get('watchpoint_updates') or [])} watchpoint updates.")
            source = self.sources.add("STATE", f"DIGEST:{digest['pack']}:{digest['day']}", text=counts, names=("GRIT-Scan", "the daily digest"),
                                      badge=f"{label} daily digest, {digest['day']}", detail={"digest": digest["day"], "pack": digest["pack"]})
            out.append({"id": source.source_id, "summary": counts, "pack": digest["pack"], "label": label, "day": digest["day"], "new_kept_pages": digest.get("kept_new"),
                        "tier_1_2_events": events, "red_patterns": digest.get("red_patterns"), "red_drafts": len(digest.get("red_drafts") or []),
                        "moved_standing_items": moved, "watchpoint_updates": len(digest.get("watchpoint_updates") or [])})
        return {"since": start.isoformat(), "days": out}

    def get_scenarios(self, shift: str | None = None) -> dict[str, Any]:
        """The reviewed best, medium and worst case for STE under each strategic shift, and where the latest run's signposts
        lean. Only for packs with a reviewed scenario register (the Group pack)."""
        from grit.ontology.pack import load_ontology_pack
        from grit.pipeline.scenarios import lean_text, load_register, outlook

        out = []
        for pack_name in self.scope.packs:
            pack = self._packs.get(pack_name) or load_ontology_pack(self.config_dir, pack_name)
            self._packs[pack_name] = pack
            register = load_register(pack_name, self.config_dir) if pack.level == "GROUP" else None
            run = self._latest_run(pack_name)
            if register is None or run is None:
                continue
            reading = {item["themeId"]: item for item in (outlook(self.pool, run["run_id"], pack, config_dir=self.config_dir) or {}).get("shifts") or []}
            for item in register["shifts"]:
                if shift and shift.casefold() not in f"{item['themeId']} {item['name']}".casefold():
                    continue
                now = reading.get(item["themeId"])
                lean = lean_text(now) if now else "Not tracked in the latest run."
                tracker = self._state(f"TRACK:{run['run_id']}:{item['themeId']}", f"{item['name']}: {lean}", f"Scenario tracker, {run['run_date']}", shift=item["themeId"])
                cases = []
                for case in item["cases"]:
                    text = " ".join([case["name"], case["narrative"], *[sign["text"] for sign in case["signposts"]], *case.get("impact", []),
                                     *[f"{response['action']} {response['owner']}" for response in case.get("responses") or []]])
                    source = self._state(f"SCN:{register.get('version')}:{item['themeId']}:{case['case']}", text,
                                         f"Reviewed scenarios v{register.get('version')}, {case['title'] if 'title' in case else case['case']}", shift=item["themeId"], case=case["case"])
                    seen_case = next((row for row in (now or {}).get("cases") or [] if row["case"] == case["case"]), None)
                    seen = []
                    for sign in (seen_case or {}).get("signposts_seen") or []:
                        ids = [self._signal(signal).source_id for signal in (self.pool.signal(signal_id) for signal_id in sign["signal_ids"][:3]) if signal]
                        seen.append({"signpost": sign["text"], "signals": ids})
                    cases.append({"id": source.source_id, "case": case["case"], "name": case["name"], "narrative": case["narrative"],
                                  "signposts": [sign["text"] for sign in case["signposts"]], "impact": case.get("impact"), "responses": case.get("responses"),
                                  "signposts_seen": seen, "responses_triggered": (seen_case or {}).get("responses_triggered") or []})
                out.append({"pack": pack_name, "shift": item["themeId"], "name": item["name"], "tracker_id": tracker.source_id, "lean": lean,
                            "reviewed": f"v{register.get('version')} by {register.get('reviewedBy')} on {register.get('reviewedOn')}", "cases": cases})
        if not out:
            return {"shifts": [], "note": "No reviewed scenario register in this reader's packs" + (f" for '{shift}'." if shift else ".")}
        return {"shifts": out, "note": "Leans are counts of signposts seen, not odds."}

    def newest_signal(self) -> str | None:
        marks = ", ".join("?" for _ in self.scope.packs)
        row = self.pool.one(f"SELECT MAX(s.published_at) AS newest FROM assessments a JOIN signals s ON s.signal_id = a.signal_id WHERE a.pack IN ({marks}) AND a.decision = 'KEEP'", self.scope.packs)
        return _date((row or {}).get("newest"))

    def search_library(self, query: str, topic: str | None = None) -> dict[str, Any]:
        """Report passages and State of the Situation sections that bear on the question."""
        from grit.library import store
        from grit.library.catalogue import REPORTS, short_name, topic as get_topic

        library_dir = self.library_dir or store.LIBRARY_DIR
        wanted = [item.report_id for item in REPORTS]
        chosen_topic = None
        if topic:
            try:
                chosen_topic = get_topic(topic)
                wanted = list(chosen_topic.reports) or wanted
            except KeyError:
                pass
        wanted = [report_id for report_id in wanted if (library_dir / f"{report_id}.jsonl").is_file()]
        passages = []
        if wanted:
            try:
                found = store.retrieve(query, wanted, k=8, per_report_floor=0, per_report_cap=3, library_dir=library_dir, embedder=self.embedder)
            except Exception as exc:  # the library is optional; a failure is reported, not raised
                found = []
                passages.append({"error": f"The library search failed: {type(exc).__name__}."})
            from grit.library.catalogue import report as get_report

            for passage, _score in found:
                item = get_report(passage.report_id)
                badge = f"{short_name(item)}, {item.edition}, {passage.page_label} {passage.page}"
                source = self.sources.add(
                    "REPORT", f"P:{passage.passage_id}", text=passage.text, names=(item.publisher, short_name(item), *item.aliases), badge=badge, url=item.url,
                    note=INTERNAL_NOTE if item.access == "INTERNAL" else None, detail={"report_id": item.report_id, "title": item.title, "page": passage.page},
                )
                passages.append({"id": source.source_id, "report": item.title, "publisher": item.publisher, "edition": item.edition,
                                 "page": f"{passage.page_label} {passage.page}", "access": item.access, "text": passage.text})
        sections = self._sots_sections(query, chosen_topic.topic_id if chosen_topic else None)
        return {"passages": passages, "state_of_the_situation": sections,
                "note": None if passages or sections else "The report library holds nothing on this."}

    def _sots_sections(self, query: str, topic_id: str | None) -> list[dict[str, Any]]:
        from grit.library.sots import render_text

        folders = sorted(path for path in self.sots_dir.glob("*") if path.is_dir()) if self.sots_dir.is_dir() else []
        if not folders:
            return []
        rows = []
        for path in sorted(folders[-1].glob("SOTS-*.json")):
            document = json.loads(path.read_text(encoding="utf-8"))
            if topic_id and document["topicId"] != topic_id:
                continue
            table = document.get("passages") or {}
            for section in document.get("sections") or []:
                if section.get("status") != "DRAFTED":
                    continue
                draft = section.get("draft") or {}
                parts = [render_text(text, table) for text in draft.get("paragraphs") or []]
                parts += [f"{entry.get('uncertainty') or entry.get('indicator')}: {render_text(entry.get('why_it_matters') or entry.get('reading') or '', table)}" for entry in draft.get("items") or []]
                text = " ".join(part for part in parts if part).strip()
                if text:
                    rows.append((document, section, text))
        if not rows:
            return []
        ranked = self._rank_texts(query, [text for _d, _s, text in rows])[:3]
        out = []
        for number in ranked:
            document, section, text = rows[number]
            names = tuple(dict.fromkeys(str(row.get("publisher")) for row in (document.get("passages") or {}).values() if row.get("publisher")))
            source = self.sources.add("SOTS", f"SOTS:{document['topicId']}:{section['key']}", text=text, names=("State of the Situation", *names),
                                      badge=f"State of the Situation: {document['name']}, {section['title']}", note=SOTS_NOTE,
                                      detail={"topic": document["topicId"], "section": section["key"]})
            out.append({"id": source.source_id, "document": document["name"], "section": section["title"], "status": "draft, not signed off", "text": text[:2500]})
        return out

    def _rank_texts(self, query: str, texts: Sequence[str]) -> list[int]:
        """Cosine order through the library's vector cache when an embedder is set, else word overlap."""
        if self.embedder is not None:
            try:
                from grit.library.store import LIBRARY_DIR
                from grit.retrieval.similarity import dot
                from grit.retrieval.store import VectorStore

                vector_store = VectorStore((self.library_dir or LIBRARY_DIR) / "vectors.sqlite")
                try:
                    vectors = vector_store.vectors(list(texts), task="RETRIEVAL_DOCUMENT", embedder=self.embedder)
                    query_vector = vector_store.vectors([query], task="RETRIEVAL_QUERY", embedder=self.embedder)[0]
                finally:
                    vector_store.close()
                return sorted(range(len(texts)), key=lambda number: -dot(query_vector, vectors[number]))
            except Exception:
                pass
        asked = {word.casefold() for word in re.findall(r"[A-Za-z]{4,}", query)}
        return sorted(range(len(texts)), key=lambda number: -len(asked & {word.casefold() for word in re.findall(r"[A-Za-z]{4,}", texts[number])}))

    def lookup_register(self, kind: str, name: str | None = None, country: str | None = None, pack: str | None = None) -> dict[str, Any]:
        """Entities, customers, suppliers or competitors in the business-area registers within reach."""
        from grit.ontology.pack import load_ontology_pack

        fields = {"entities": ("entities", "entityName"), "customers": ("customers", "customerName"), "suppliers": ("suppliers", "supplierName")}
        kind = (kind or "").casefold()
        if kind not in {*fields, "competitors"}:
            return {"error": "kind is one of entities, customers, suppliers, competitors."}
        packs = [pack] if pack else list(self.scope.packs)
        refused = [item for item in packs if item not in self.scope.packs]
        if refused:
            return {"error": f"{', '.join(refused)} is outside this reader's packs."}
        rows = []
        for item in packs:
            if item not in self._packs:
                self._packs[item] = load_ontology_pack(self.config_dir, item)
            loaded = self._packs[item]
            if kind == "competitors":
                records, label_key = list((loaded.competitors or {}).get("competitors") or []), None
            else:
                records, label_key = list(loaded.exposure.get(fields[kind][0]) or []), fields[kind][1]
            for record in records:
                if not isinstance(record, Mapping):
                    continue
                flat = {key: value for key, value in record.items() if isinstance(value, (str, int, float)) and value not in ("", None)}
                label = str(record.get(label_key) or next(iter(flat.values()), "")) if label_key else str(next((value for key, value in flat.items() if "name" in key.casefold()), ""))
                blob = " ".join(str(value) for value in flat.values()).casefold()
                if name and name.casefold() not in blob:
                    continue
                if country and country.casefold() not in blob:
                    continue
                rows.append((item, label, flat))
        out = []
        for item, label, flat in rows[:MAX_REGISTER]:
            text = "; ".join(f"{key}: {value}" for key, value in list(flat.items())[:10])
            source = self.sources.add("REGISTER", f"REG:{item}:{kind}:{label}", text=text, names=(f"{self.scope.label(item)} register",), badge=f"{self.scope.label(item)} register ({kind})")
            out.append({"id": source.source_id, "pack": item, "name": label, "fields": dict(list(flat.items())[:10])})
        return {"kind": kind, "rows": out, "total": len(rows), "note": f"{len(rows) - MAX_REGISTER} more not shown." if len(rows) > MAX_REGISTER else None}

    # Dispatch -----------------------------------------------------------------------------------------------------

    SPECS = {
        "get_state": ("pack", "run_id"),
        "condition_history": ("item_id", "since"),
        "search_signals": ("query", "include_dropped"),
        "search_library": ("query", "topic"),
        "lookup_register": ("kind", "name", "country", "pack"),
        "web_search": ("query",),
        "whats_new": ("since", "pack"),
        "get_scenarios": ("shift",),
    }

    def call(self, name: str, args: Mapping[str, Any] | None) -> dict[str, Any]:
        args = dict(args or {})
        if name not in self.SPECS:
            result: dict[str, Any] = {"error": f"Unknown tool {name}. Tools: {', '.join(self.SPECS)}."}
        else:
            unknown = sorted(set(args) - set(self.SPECS[name]))
            wrong = sorted(key for key, value in args.items() if key != "include_dropped" and value is not None and not isinstance(value, str))
            try:
                if unknown:
                    result = {"error": f"Unknown argument {', '.join(unknown)} for {name}."}
                elif wrong:
                    result = {"error": f"{', '.join(wrong)} must be text for {name}."}
                else:
                    result = getattr(self, name)(**args)
            except Exception as exc:  # a lookup that fails is reported to the model, never raised into the chat
                result = {"error": f"{name} failed: {type(exc).__name__}."}
        self.calls.append({"tool": name, "args": args, "error": result.get("error"), "sources": len(self.sources)})
        return result
