"""Reviewed best, medium and worst case scenarios, and the monthly signpost tracker (spec 43, steps 2 to 5).

- Approve: a reviewed draft becomes the pack's scenario register, config/<pack>/<PACK>-Scenarios.json. The Keyworder gives
  each signpost and counter-indicator two to four keyword phrases drawn from its own text; the reviewer may edit them. The
  previous register is kept in config/<pack>/history/.
- Track: for a run, count the kept signals of the pack since the previous weekly or monthly run (daily runs included) that
  match each case's signposts and counter-indicators. A signal must touch the shift (a strong hit on the shift, or a hit on
  a Group condition the shift covers) and name at least two of an item's keywords, or its only keyword. One of the
  keywords it names must be distinctive: used by no other signpost or counter-indicator of the shift. A place or name that
  every Middle East signpost shares does not, alone, show which case is unfolding. An item with no distinctive keyword
  needs all its keywords. A keyword that the period's news uses everywhere (in more than 8% of the shift's signals, and at
  least 5) is common too, and does not count as distinctive. The case with the most signposts seen is where the evidence
  leans. These are counts, never odds.
- Judge (from 2 November 2026): keyword matching misses most sightings, because the signposts were written for a 1-5 year
  horizon and weekly news rarely uses their phrases. For each signpost and counter-indicator, the signals touching the
  shift are ranked by embedding similarity to its text. The closest JUDGE_CANDIDATES above JUDGE_FLOOR go to the Signpost
  Judge, which names the reports that show the development, each with a verbatim quote that a check confirms. Its matches
  are stored on the run (config "signposts_seen") and count as seen, together with keyword matches.
- Outlook: what a Group brief, deck and the Co-Pilot show: the lean per shift, the signposts seen with their sources, the
  counter-indicators seen, and the planned responses whose trigger signpost has been seen.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from grit.pool import Pool
from grit.settings import CONFIG_DIR

REVIEWED = "REVIEWED"
JUDGE_CANDIDATES = 3  # closest signals per signpost or counter-indicator sent to the Signpost Judge
JUDGE_FLOOR = 0.60  # cosine below this is not a candidate
JUDGE_SOURCES = 18  # distinct candidate reports per shift
LEAD_CHARS = 800
COMMON_SHARE, COMMON_MIN = 0.08, 5  # a keyword in more of the period's shift signals than this is common, not distinctive
CASE_TITLES = {"BEST": "Best case for STE", "MEDIUM": "Medium case for STE", "WORST": "Worst case for STE"}
LEAN_WORDS = {"BEST": "towards the best case", "MEDIUM": "towards the medium case", "WORST": "towards the worst case", "MIXED": "mixed", "NONE": "no signpost seen"}


def register_path(pack: str, config_dir: Path | None = None) -> Path:
    return (config_dir or CONFIG_DIR) / pack / f"{pack.upper()}-Scenarios.json"


def load_register(pack: str, config_dir: Path | None = None) -> dict[str, Any] | None:
    """The reviewed register, or None when there is none or it is not reviewed."""
    path = register_path(pack, config_dir)
    if not path.is_file():
        return None
    try:
        register = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}.") from exc
    return register if register.get("status") == REVIEWED else None


# Approve -------------------------------------------------------------------------------------------------------------


def _item_text(item: Any) -> str:
    return str(item.get("text") if isinstance(item, Mapping) else item or "")


def _earlier_keywords(pack: str, config_dir: Path | None) -> dict[str, list[str]]:
    """Item text -> its most recent non-empty keywords, from the register and its history, newest first."""
    path = register_path(pack, config_dir)
    files = [path] if path.is_file() else []
    history = path.parent / "history"
    if history.is_dir():
        files += sorted(history.glob(f"{path.stem}_v*.json"), key=lambda item: int(item.stem.rsplit("_v", 1)[-1]), reverse=True)
    found: dict[str, list[str]] = {}
    for file in files:
        register = json.loads(file.read_text(encoding="utf-8"))
        for shift in register.get("shifts") or []:
            for case in shift.get("cases") or []:
                for item in [*case.get("signposts", []), *case.get("counter_indicators", [])]:
                    if isinstance(item, Mapping) and item.get("keywords") and item.get("text") not in found:
                        found[item["text"]] = list(item["keywords"])
    return found


def approve(
    pool: Pool, pack: str, run_date: date, *, reviewed_by: str, client: Any, model: str, config_dir: Path | None = None, draft_path: Path | None = None,
    reviewed_on: date | None = None, log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Make the run's proposed draft the reviewed register, with keywords for every signpost and counter-indicator."""
    from grit.agents import reader as agents
    from grit.pipeline.reader_scan import ReaderRun, run_id_for

    run = ReaderRun(run_id_for(run_date, pack), run_date, pack)
    draft_path = draft_path or run.folder / "scenarios" / f"{pack.upper()}-Scenarios-BMW-Proposed_{run_date.isoformat()}.json"
    if not draft_path.is_file():
        raise FileNotFoundError(f"No proposed draft at {draft_path}. Run grit scenarios --pack {pack} --date {run_date} first.")
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    earlier = _earlier_keywords(pack, config_dir)
    path = register_path(pack, config_dir)
    current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    reviewed_before = {item["themeId"]: item for item in current.get("shifts") or []} if current.get("status") == REVIEWED else {}
    shifts, withheld, carried = [], [], []
    for shift in draft["shifts"]:
        if shift["status"] != "PROPOSED":
            if shift["themeId"] in reviewed_before:
                # A refresh that could not redraft a shift keeps its reviewed cases, so the register never shrinks.
                shifts.append(reviewed_before[shift["themeId"]])
                carried.append(shift["themeId"])
            else:
                withheld.append(shift["themeId"])
            continue
        items: dict[str, str] = {}
        for case in shift["cases"]:
            for number, text in enumerate(case["signposts"], start=1):
                items[f"{case['case']}-S{number}"] = _item_text(text)
            for number, text in enumerate(case["counter_indicators"], start=1):
                items[f"{case['case']}-C{number}"] = _item_text(text)
        result, call = agents.propose_keywords(client, model, subject=shift["themeId"], items=items)
        pool.log(actor_type="AGENT", run_id=run.run_id, **call.as_record())
        keywords = result["keywords"]
        if result["status"] != "PROPOSED":
            # Keep the last reviewed keywords for any item whose text is unchanged, so a failed call never blanks the tracker.
            keywords = {key: earlier[items[key]] for key in items if items[key] in earlier}
        log(f"  Keyworder {shift['themeId']:14} {result['status']:9} items {len(items)}")
        cases = []
        for case in shift["cases"]:
            cases.append({
                **{key: case[key] for key in ("case", "title", "name", "narrative", "evidence", "impact", "responses")},
                **({"where_ste_could_gain": case["where_ste_could_gain"]} if case.get("where_ste_could_gain") else {}),
                "signposts": [{"text": _item_text(text), "keywords": keywords.get(f"{case['case']}-S{number}", [])} for number, text in enumerate(case["signposts"], start=1)],
                "counter_indicators": [{"text": _item_text(text), "keywords": keywords.get(f"{case['case']}-C{number}", [])} for number, text in enumerate(case["counter_indicators"], start=1)],
                "sources": {"signals": case.get("signals") or [], "reports": case.get("reports") or []},
            })
        shifts.append({"themeId": shift["themeId"], "name": shift["name"], "horizon": shift.get("horizon") or "1-5 years", "situation": shift.get("situation"),
                       "builds_on": shift.get("builds_on") or [], "keywords": result["status"], "cases": cases})
    history = path.parent / "history"
    archived = [int(item.stem.rsplit("_v", 1)[-1]) for item in history.glob(f"{path.stem}_v*.json") if item.stem.rsplit("_v", 1)[-1].isdigit()] if history.is_dir() else []
    current_version = current.get("version") if isinstance(current.get("version"), int) else 0
    slot = current_version if current_version and current_version not in archived else max(archived, default=0) + 1
    version = max([current_version, slot, *archived]) + 1 if path.is_file() else max(archived, default=0) + 1
    if path.is_file():
        history.mkdir(parents=True, exist_ok=True)
        target = history / f"{path.stem}_v{slot}.json"
        if target.exists():
            raise FileExistsError(f"{target} already exists. History is never overwritten; check the register's version number.")
        shutil.copyfile(path, target)
    register = {
        "registerId": f"{pack.upper()}-SCENARIOS", "pack": pack, "status": REVIEWED, "version": version,
        "reviewedBy": reviewed_by, "reviewedOn": (reviewed_on or date.today()).isoformat(), "approvedFrom": str(draft_path.name), "run": run.run_id,
        "pointOfView": "Best and worst are judged for ST Engineering.", "horizon": "1-5 years",
        "note": "Reviewed scenarios. Signposts show which case is unfolding; they are counts, never odds. Edit keywords here if a signpost matches badly.",
        "shifts": shifts, "notReviewed": withheld, "carriedForward": carried,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(register, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    pool.log(actor_type="FUNCTION", actor="ScenarioApproval", status=REVIEWED, run_id=run.run_id, subject=register["registerId"],
             detail={"version": version, "reviewed_by": reviewed_by, "shifts": len(shifts), "not_reviewed": withheld})
    return {"path": str(path), "version": version, "shifts": len(shifts), "not_reviewed": withheld, "carried_forward": carried,
            "keywords_withheld": [item["themeId"] for item in shifts if item["keywords"] != "PROPOSED"]}


# Track ---------------------------------------------------------------------------------------------------------------


def _patterns(keywords: Sequence[str]) -> list[Any]:
    from grit.profiles.emphasis import _phrase

    return [_phrase(keyword) for keyword in keywords if str(keyword).strip()]


def matches_item(text: str, keywords: Sequence[str], shared: frozenset[str] = frozenset()) -> bool:
    """shared: keywords (case-folded) that other items of the same shift also use."""
    keywords = [str(keyword) for keyword in keywords if str(keyword).strip()]
    if not keywords:
        return False
    hit = [keyword for keyword, pattern in zip(keywords, _patterns(keywords)) if pattern.search(text)]
    distinctive = [keyword for keyword in keywords if keyword.casefold() not in shared]
    if not distinctive:
        return len(hit) == len(keywords)
    return len(hit) >= min(2, len(keywords)) and any(keyword.casefold() not in shared for keyword in hit)


def _everyday(shift: Mapping[str, Any], texts: Sequence[str]) -> frozenset[str]:
    """Keywords the period's shift signals use so widely that naming them shows nothing about which case is unfolding."""
    keywords = {str(keyword) for case in shift["cases"] for item in [*case["signposts"], *case["counter_indicators"]] for keyword in item.get("keywords") or []}
    limit = max(COMMON_MIN, COMMON_SHARE * len(texts))
    common = set()
    for keyword in keywords:
        pattern = _patterns([keyword])[0]
        if sum(1 for text in texts if pattern.search(text)) > limit:
            common.add(keyword.casefold())
    return frozenset(common)


def _shared(shift: Mapping[str, Any]) -> dict[int, frozenset[str]]:
    """For each item of the shift (by position), the keywords another item of the same shift also uses."""
    items = [item for case in shift["cases"] for item in [*case["signposts"], *case["counter_indicators"]]]
    owners: dict[str, set[int]] = {}
    for number, item in enumerate(items):
        for keyword in item.get("keywords") or []:
            owners.setdefault(str(keyword).casefold(), set()).add(number)
    return {number: frozenset(keyword for keyword, users in owners.items() if number in users and len(users) > 1) for number in range(len(items))}


def window_rows(pool: Pool, run_id: str) -> list[dict[str, Any]]:
    """The pack's kept signals from this run and every run of the pack since the previous weekly or monthly run, one row
    per signal with its best tier and the union of its themes and conditions. This run counts while it is still open:
    a reasoned run writes its reports before it finishes."""
    run = pool.run(run_id)
    previous = pool.latest_run(run["pack"], before=run["run_date"], kind="READER_SCAN")
    params: list[Any] = [run["pack"], run_id, run["run_date"]]
    after = ""
    if previous:
        after = "AND r.run_date > ?"
        params.append(previous["run_date"])
    rows = pool.rows(
        f"""SELECT a.signal_id, a.tier, a.themes_json, a.condition_ids_json, s.title, s.text, s.publisher, s.published_at, s.url
            FROM assessments a JOIN runs r ON r.run_id = a.run_id JOIN signals s ON s.signal_id = a.signal_id
            WHERE r.pack = ? AND (r.status = 'COMPLETE' OR r.run_id = ?) AND a.decision = 'KEEP' AND r.run_date <= ? {after}""",
        params,
    )
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        held = merged.setdefault(row["signal_id"], {**row, "themes": [], "condition_ids": set()})
        held["themes"] += [item for item in row.get("themes") or [] if isinstance(item, Mapping)]
        held["condition_ids"] |= set(row.get("condition_ids") or [])
        held["tier"] = min(held.get("tier") or 9, row.get("tier") or 9)
    return list(merged.values())


def _touches(row: Mapping[str, Any], shift_id: str, conditions: set[str]) -> bool:
    return any(item.get("theme_id") == shift_id and item.get("strong") for item in row["themes"]) or bool(row["condition_ids"] & conditions)


def _lean(seen: Mapping[str, int]) -> str:
    top = max(seen.values(), default=0)
    if top == 0:
        return "NONE"
    leaders = [case for case, count in seen.items() if count == top]
    return leaders[0] if len(leaders) == 1 else "MIXED"


def _items(shift: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    """('BEST-S1', item) ... for every signpost and counter-indicator of a shift, in register order."""
    found = []
    for case in shift["cases"]:
        found += [(f"{case['case']}-S{number}", item) for number, item in enumerate(case["signposts"], start=1)]
        found += [(f"{case['case']}-C{number}", item) for number, item in enumerate(case["counter_indicators"], start=1)]
    return found


def judge_run(pool: Pool, run_id: str, pack: Any, *, client: Any, model: str, embedder: Any | None = None, log: Callable[[str], None] = print,
              config_dir: Path | None = None) -> dict[str, Any]:
    """Shortlist by embedding similarity, then the Signpost Judge confirms. Saves {themeId: {item id: [signal ids]}} on the run."""
    import numpy as np

    from grit.agents import reader as agents
    from grit.retrieval.embedder import DEFAULT_DIMENSIONS, DEFAULT_MODEL, Embedder

    register = load_register(pack.pack, config_dir or Path(pack.config_dir).parent)
    if register is None:
        return {"skipped": "no reviewed scenario register"}
    rows = window_rows(pool, run_id)
    vectors = pool.vectors([row["signal_id"] for row in rows], model=DEFAULT_MODEL, task="RETRIEVAL_DOCUMENT", dimensions=DEFAULT_DIMENSIONS)
    covered = shift_conditions(pack)
    texts = [item["text"] for shift in register.get("shifts") or [] for _key, item in _items(shift)]
    if not texts or not vectors:
        return {"skipped": "no signpost text or no signal vectors"}
    queries = iter((embedder or Embedder()).embed(texts, task="RETRIEVAL_QUERY"))
    saved: dict[str, dict[str, list[str]]] = {}
    quotes: dict[str, dict[str, list[dict[str, str]]]] = {}
    counts = {"shifts": 0, "matches": 0}
    for shift in register.get("shifts") or []:
        relevant = [row for row in rows if _touches(row, shift["themeId"], set(covered.get(shift["themeId"]) or [])) and row["signal_id"] in vectors]
        matrix = np.asarray([vectors[row["signal_id"]] for row in relevant], dtype=np.float32) if relevant else None
        if matrix is not None:
            matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
        items, picked = {}, []
        for key, item in _items(shift):
            query = np.asarray(next(queries), dtype=np.float32)
            if matrix is None:
                continue
            scores = matrix @ (query / np.linalg.norm(query))
            best = [int(index) for index in np.argsort(-scores)[:JUDGE_CANDIDATES] if scores[index] >= JUDGE_FLOOR]
            if best:
                items[key] = {"text": item["text"], "candidates": [relevant[index]["signal_id"] for index in best]}
                picked += items[key]["candidates"]
        chosen = list(dict.fromkeys(picked))[:JUDGE_SOURCES]
        if not items or not chosen:
            continue
        table, back = agents.source_table([next(row for row in relevant if row["signal_id"] == signal_id) for signal_id in chosen])
        ids = {signal_id: key for key, signal_id in back.items()}
        for value in items.values():
            value["candidates"] = [ids[signal_id] for signal_id in value["candidates"] if signal_id in ids]
        result, call = agents.judge_signposts(client, model, risk=shift["name"], items=items, sources=table, subject=shift["themeId"])
        pool.log(actor_type="AGENT", run_id=run_id, **call.as_record())
        found: dict[str, list[str]] = {}
        for match in result["matches"]:
            found.setdefault(match["item"], []).append(back[match["source_id"]])
            quotes.setdefault(shift["themeId"], {}).setdefault(match["item"], []).append({"signal_id": back[match["source_id"]], "quote": match["quote"]})
        saved[shift["themeId"]] = found
        counts["shifts"] += 1
        counts["matches"] += sum(len(value) for value in found.values())
        log(f"  SignpostJudge {shift['themeId']:14} {result['status']:9} candidates {len(chosen):2}  matches {sum(len(value) for value in found.values())}")
    pool.update_run_config(run_id, signposts_seen=saved, signpost_quotes=quotes)
    return counts


def editorial_exclusions(pool: Pool, run_id: str) -> set[str]:
    """Signals the reviewed weekly selection excluded. They are not evidence for a signpost. Empty before review."""
    if (((pool.run(run_id) or {}).get("config") or {}).get("weekly_mode")) != "reasoned":
        return set()
    try:
        rows = pool.weekly_assessments(run_id, include_excluded=True)
    except ValueError:
        return set()
    return {row["signal_id"] for row in rows if (row.get("editorial") or {}).get("decision") == "EXCLUDE"}


def track(pool: Pool, run_id: str, register: Mapping[str, Any], *, shift_conditions: Mapping[str, Sequence[str]] | None = None) -> list[dict[str, Any]]:
    """One reading per shift: for each case, the signposts and counter-indicators seen with their signal ids, and the lean.
    Seen means a keyword match or a Signpost Judge match stored on the run."""
    judged = (((pool.run(run_id) or {}).get("config") or {}).get("signposts_seen") or {})
    excluded = editorial_exclusions(pool, run_id)
    rows = [row for row in window_rows(pool, run_id) if row["signal_id"] not in excluded]
    readings = []
    for shift in register.get("shifts") or []:
        conditions = set((shift_conditions or {}).get(shift["themeId"]) or [])
        relevant = [row for row in rows if _touches(row, shift["themeId"], conditions)]
        texts = {row["signal_id"]: f"{row.get('title') or ''}\n{str(row.get('text') or '')[:LEAD_CHARS]}" for row in relevant}
        everyday = _everyday(shift, list(texts.values()))
        shared, position = {number: words | everyday for number, words in _shared(shift).items()}, 0
        cases, seen_counts = [], {}
        confirmed = judged.get(shift["themeId"]) or {}
        for case in shift["cases"]:
            seen = []
            for number, item in enumerate(case["signposts"], start=1):
                common, position = shared[position], position + 1
                ids = sorted({*(signal_id for signal_id, text in texts.items() if matches_item(text, item.get("keywords") or [], common)), *confirmed.get(f"{case['case']}-S{number}", [])} - excluded)
                if ids:
                    seen.append({"number": number, "text": item["text"], "signal_ids": ids})
            against = []
            for number, item in enumerate(case["counter_indicators"], start=1):
                common, position = shared[position], position + 1
                ids = sorted({*(signal_id for signal_id, text in texts.items() if matches_item(text, item.get("keywords") or [], common)), *confirmed.get(f"{case['case']}-C{number}", [])} - excluded)
                if ids:
                    against.append({"number": number, "text": item["text"], "signal_ids": ids})
            fired = {item["number"] for item in seen}
            triggered = [dict(response) for response in case.get("responses") or [] if response.get("trigger") in fired]
            seen_counts[case["case"]] = len(seen)
            cases.append({"case": case["case"], "title": CASE_TITLES.get(case["case"], case["case"]), "name": case["name"],
                          "where_ste_could_gain": case.get("where_ste_could_gain") or None,
                          "signposts_seen": seen, "counter_indicators_seen": against, "responses_triggered": triggered, "signposts_total": len(case["signposts"])})
        readings.append({"themeId": shift["themeId"], "name": shift["name"], "signals_considered": len(relevant), "lean": _lean(seen_counts), "seen": seen_counts, "cases": cases})
    return readings


def shift_conditions(pack: Any) -> dict[str, list[str]]:
    """The Group conditions each shift covers, from the baseline's steThemes."""
    names = {row["themeName"]: row["themeId"] for row in (pack.themes or {}).get("themes") or []}
    found: dict[str, list[str]] = {}
    for condition in (pack.baseline or {}).get("conditions") or []:
        for name in condition.get("steThemes") or []:
            if name in names:
                found.setdefault(names[name], []).append(condition["id"])
    return found


def outlook(pool: Pool, run_id: str, pack: Any, *, config_dir: Path | None = None) -> dict[str, Any] | None:
    """The scenario outlook for a brief: this run's reading against the reviewed register, the previous run's lean,
    and source references for every signal seen. None when the pack has no reviewed register."""
    register = load_register(pack.pack, config_dir or Path(pack.config_dir).parent)
    if register is None:
        return None
    covered = shift_conditions(pack)
    now = track(pool, run_id, register, shift_conditions=covered)
    run = pool.run(run_id)
    previous_run = pool.latest_run(pack.pack, before=run["run_date"], kind="READER_SCAN")
    before = {item["themeId"]: item["lean"] for item in track(pool, previous_run["run_id"], register, shift_conditions=covered)} if previous_run else {}

    def ref(signal_id: str) -> dict[str, Any] | None:
        from grit.report.brief import fmt_date

        signal = pool.signal(signal_id)
        return {"signal_id": signal_id, "outlet": signal["publisher"], "date": fmt_date(signal["published_at"]), "url": signal["url"], "title": signal["title"]} if signal else None

    quotes = ((run.get("config") or {}).get("signpost_quotes") or {})
    for reading in now:
        reading["previous_lean"] = before.get(reading["themeId"])
        for case in reading["cases"]:
            for kind, items in (("S", case["signposts_seen"]), ("C", case["counter_indicators_seen"])):
                for item in items:
                    item["quotes"] = [entry["quote"] for entry in (quotes.get(reading["themeId"]) or {}).get(f"{case['case']}-{kind}{item['number']}", [])]
            for item in [*case["signposts_seen"], *case["counter_indicators_seen"]]:
                item["sources"] = [found for found in (ref(signal_id) for signal_id in item["signal_ids"][:3]) if found]
                item["more"] = max(0, len(item["signal_ids"]) - 3)
    from grit.pipeline.indicators import score_indicators

    root = config_dir or Path(pack.config_dir).parent
    try:
        from grit.ontology.risk_cascade import load_risk_cascade

        risk_links = load_risk_cascade(root, pack).links()
    except (OSError, KeyError, json.JSONDecodeError):
        risk_links = []
    return {
        "version": register.get("version"), "reviewed_by": register.get("reviewedBy"), "reviewed_on": register.get("reviewedOn"),
        "shifts": now, "indicators": score_indicators(window_rows(pool, run_id)), "risk_links": risk_links,
    }


def lean_text(reading: Mapping[str, Any]) -> str:
    """'Leaning to the worst case (2 of 4 signposts seen); was mixed.'"""
    lean = reading["lean"]
    if lean in {"BEST", "MEDIUM", "WORST"}:
        case = next(item for item in reading["cases"] if item["case"] == lean)
        text = f"Leaning {LEAN_WORDS[lean]} ({len(case['signposts_seen'])} of {case['signposts_total']} signposts seen)"
    elif lean == "MIXED":
        text = "Mixed: " + ", ".join(f"{case} {count}" for case, count in reading["seen"].items() if count) + " signposts seen"
    else:
        text = "No signpost seen this period"
    previous = reading.get("previous_lean")
    if previous and previous != lean:
        text += f"; previously {LEAN_WORDS.get(previous, previous.lower())}"
    return text + "."


# Quarterly refresh ---------------------------------------------------------------------------------------------------


def reviewed_view(pool: Pool, run_id: str, pack: Any, shift_id: str, *, config_dir: Path | None = None) -> dict[str, Any] | None:
    """What the Strategist sees on a refresh: the reviewed cases for the shift, and the signposts news has matched since."""
    register = load_register(pack.pack, config_dir or Path(pack.config_dir).parent)
    if register is None:
        return None
    shift = next((item for item in register["shifts"] if item["themeId"] == shift_id), None)
    if shift is None:
        return None
    reading = next(item for item in track(pool, run_id, {"shifts": [shift]}, shift_conditions=shift_conditions(pack)))
    return {
        "version": register.get("version"), "reviewed_on": register.get("reviewedOn"), "builds_on": shift.get("builds_on"),
        "cases": [{"case": case["case"], "name": case["name"], "narrative": case["narrative"], "signposts": [item["text"] for item in case["signposts"]],
                   "counter_indicators": [item["text"] for item in case["counter_indicators"]], "where_ste_could_gain": case.get("where_ste_could_gain")} for case in shift["cases"]],
        "signposts_seen": {case["case"]: [item["text"] for item in case["signposts_seen"]] for case in reading["cases"]},
    }


def changes(reviewed: Mapping[str, Any] | None, proposed: Mapping[str, Any]) -> list[str]:
    """Plain lines on what a refreshed shift changes against its reviewed version."""
    if not reviewed:
        return ["New: no reviewed version of this shift yet."]
    lines = []
    old = {case["case"]: case for case in reviewed["cases"]}
    for case in proposed.get("cases") or []:
        before = old.get(case["case"])
        title = CASE_TITLES.get(case["case"], case["case"])
        if before is None:
            lines.append(f"{title}: new.")
            continue
        if before["name"] != case["name"]:
            lines.append(f"{title}: renamed from '{before['name']}' to '{case['name']}'.")
        if before["narrative"] != case["narrative"]:
            lines.append(f"{title}: narrative changed.")
        old_posts = {_item_text(item) for item in before["signposts"]}
        new_posts = [_item_text(item) for item in case["signposts"]]
        added = [text for text in new_posts if text not in old_posts]
        removed = [text for text in old_posts if text not in set(new_posts)]
        lines += [f"{title}: new signpost: {text}" for text in added] + [f"{title}: dropped signpost: {text}" for text in removed]
        if (before.get("where_ste_could_gain") or "") != (case.get("where_ste_could_gain") or ""):
            lines.append(f"{title}: the gain hypothesis changed.")
    return lines or ["No change from the reviewed version."]


# Commands ------------------------------------------------------------------------------------------------------------


def approve_main(argv: list[str] | None = None) -> int:
    from grit.cli import _client, _model
    from grit.settings import load_local_env

    parser = argparse.ArgumentParser(prog="grit scenarios-approve", description="Record the reviewer's approval of a proposed BMW draft: it becomes the pack's scenario register, with signpost keywords.")
    parser.add_argument("--pack", default="ste")
    parser.add_argument("--date", type=date.fromisoformat, required=True, help="The run date of the reviewed draft, YYYY-MM-DD.")
    parser.add_argument("--by", required=True, help="Who reviewed it, such as \"Wilson Yeo\".")
    parser.add_argument("--draft", type=Path, help="The edited draft JSON, if the reviewer's edits were saved to another file.")
    parser.add_argument("--model")
    args = parser.parse_args(argv)
    load_local_env()
    result = approve(Pool(), args.pack, args.date, reviewed_by=args.by, client=_client(), model=_model(args.model), draft_path=args.draft)
    print(json.dumps(result, indent=2))
    return 0


def track_main(argv: list[str] | None = None) -> int:
    from grit.pipeline.reader_scan import _load_pack, run_id_for

    parser = argparse.ArgumentParser(prog="grit scenarios-track", description="Show which case each shift's signposts lean to for a run. Counts, never odds.")
    parser.add_argument("--pack", default="ste")
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    args = parser.parse_args(argv)
    pool = Pool()
    result = outlook(pool, run_id_for(args.date, args.pack), _load_pack(args.pack))
    if result is None:
        print(f"No reviewed scenario register for {args.pack}. Approve a draft with grit scenarios-approve first.")
        return 0
    print(f"Register v{result['version']}, reviewed by {result['reviewed_by']} on {result['reviewed_on']}.")
    for reading in result["shifts"]:
        print(f"{reading['themeId']}  {reading['name']}: {lean_text(reading)}  (signals considered: {reading['signals_considered']})")
        for case in reading["cases"]:
            for item in case["signposts_seen"]:
                print(f"    {case['case']:6} signpost {item['number']}: {len(item['signal_ids'])} signal(s)")
            for response in case["responses_triggered"]:
                print(f"    {case['case']:6} response triggered: {response['action']} ({response['owner']})")
            if case.get("where_ste_could_gain"):
                print(f"    {case['case']:6} Hypothesis: to validate with BAs. {case['where_ste_could_gain']}")
    for item in result.get("indicators") or []:
        bounds = "bounds not set" if item["green_max"] is None or item["amber_max"] is None else f"green at or under {item['green_max']}, amber at or under {item['amber_max']}"
        print(f"Indicator  {item['name']}: {item['signals']} signal(s), band {item['band']} ({bounds}).")
    return 0

