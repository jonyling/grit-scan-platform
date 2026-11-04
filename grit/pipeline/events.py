"""The week's events for brief format v2 (spec 44): the week's kept signals grouped into events, each with its risk linkage
and a band against the previous report. Deterministic, apart from the escalating-items rows, which the EventAssessor drafts
and the checks decide.

| Band | Rule |
|---|---|
| ▲ | A material event that is new since the previous report, or that touches a risk whose trend this run is Escalating. |
| = | Any other event with a report this week. |
| ▼ | A material event of the previous report with no report this week. |

A material event is RED, or tier 1, or tier 2 with a risk linkage and either two or more independent publishers or a direct or
counterparty link to the business area. An elevated event is a ▲ event, or a continuing material event that is tier 1 or
RED. The escalating-items table shows the elevated events, at most MAX_ROWS.

In a reasoned run the reviewed selection decides instead (URS methodology, section 13). A material event has a member with
a significance score of 8 or more, an immediate threat, or RED impact confirmed by R&A, and every material event is elevated.
Events are ordered by score within each band, and the best-sourced report leads. A selection that cites another candidate
as reporting the same event joins the two events.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping, Sequence

from grit.pool import Pool

UP, SAME, DOWN = "▲", "=", "▼"
MAX_ROWS = 8  # escalating and elevated rows drafted per run
MAX_SOURCES = 6  # reports per event shown to the EventAssessor
_THEME_ID = re.compile(r"-(KEY|TIER_2|EMERGING)-(\d+)$")
_KIND = {"KEY": "Key Risk", "TIER_2": "Tier-2 Risk", "EMERGING": "Emerging Risk"}


def risk_labels(ids: Sequence[str], names: Mapping[str, str]) -> str:
    """'Key Risks 5, 3; Tier-2 Risk 7' for business-area themes, in the register's numbering. A condition keeps its name."""
    grouped: dict[str, list[str]] = {}
    others: list[str] = []
    for item in dict.fromkeys(ids):
        match = _THEME_ID.search(item)
        if match:
            grouped.setdefault(match.group(1), []).append(str(int(match.group(2))))
        elif item in names:
            others.append(names[item])
    parts = [f"{_KIND[kind]}{'s' if len(numbers) > 1 else ''} {', '.join(numbers)}" for kind, numbers in grouped.items()]
    return "; ".join([*parts, *others])


def _strong_themes(row: Mapping[str, Any]) -> list[str]:
    found = [str(item.get("theme_id")) for item in row.get("themes") or [] if isinstance(item, Mapping) and item.get("strong") and item.get("theme_id")]
    return list(dict.fromkeys([*found, *(str(item) for item in row.get("condition_ids") or [])]))


def _event_ids(pool: Pool, signal_ids: Sequence[str]) -> dict[str, str]:
    if not signal_ids:
        return {}
    marks = ",".join("?" for _ in signal_ids)
    found = {row["signal_id"]: row["event_id"] for row in pool.rows(f"SELECT signal_id, event_id FROM event_members WHERE signal_id IN ({marks})", list(signal_ids))}
    return {signal_id: found.get(signal_id) or f"EVT-{signal_id[4:]}" for signal_id in signal_ids}


def _group(pool: Pool, rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    """Signals grouped by event cluster. In a reasoned run, a selection that cites other candidates as corroborating or
    contradicting the same event joins their events, so one event is not reported twice."""
    mapping = _event_ids(pool, [row["signal_id"] for row in rows])
    parent = {event_id: event_id for event_id in mapping.values()}

    def root(event_id: str) -> str:
        while parent[event_id] != event_id:
            parent[event_id] = parent[parent[event_id]]
            event_id = parent[event_id]
        return event_id

    from grit.core.headlines import distinctive_terms

    titles = {row["signal_id"]: distinctive_terms(str(row.get("title") or "")) for row in rows}
    for row in rows:
        editorial = row.get("editorial") if isinstance(row.get("editorial"), Mapping) else {}
        for cited in editorial.get("source_ids") or []:
            # A selection may cite context about another event; only a citation whose headline shares a word joins events.
            if cited in mapping and cited != row["signal_id"] and titles[row["signal_id"]] & titles.get(cited, frozenset()):
                first, second = sorted((root(mapping[row["signal_id"]]), root(mapping[cited])))
                parent[second] = first
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(root(mapping[row["signal_id"]]), []).append(row)
    return grouped


def significance_total(row: Mapping[str, Any]) -> int | None:
    """The reviewed significance score (URS methodology, section 13), or None before review."""
    editorial = row.get("editorial") if isinstance(row.get("editorial"), Mapping) else {}
    score = editorial.get("significance")
    return sum(int(value) for value in score.values()) if isinstance(score, Mapping) else None


def _band_rank(row: Mapping[str, Any]) -> int:
    from grit.core.source_bands import BANDS

    band = str(row.get("source_band") or "UNRATED")
    return BANDS.index(band) if band in BANDS else len(BANDS)


def red_status(row: Mapping[str, Any]) -> str | None:
    """A rule raises a RED candidate. Only an R&A decision confirms or rejects its impact."""
    if not row.get("red"):
        return None
    return str(row.get("red_status") or "CANDIDATE")


def _live_red(row: Mapping[str, Any]) -> bool:
    return red_status(row) in {"CANDIDATE", "CONFIRMED"}


ELEVATED_SCORE = 8


def _elevated(members: Sequence[Mapping[str, Any]]) -> bool:
    """Material: RED, tier 1, or tier 2 with a risk linkage and corroboration or a direct link. In a reasoned run the
    reviewed significance decides instead: a score of 8 or more (significant watch or top priority), an immediate threat,
    or RED impact confirmed by R&A (URS methodology, section 13)."""
    from grit.core.priority import outlet_groups

    if not members:
        return False
    if any(significance_total(row) is not None for row in members):
        return any(red_status(row) == "CONFIRMED" or (significance_total(row) or 0) >= ELEVATED_SCORE
                   or bool((row.get("editorial") or {}).get("immediate_threat")) for row in members)
    tier = min((row.get("tier") or 9) for row in members)
    if any(_live_red(row) for row in members) or tier == 1:
        return True
    linked = any(_strong_themes(row) for row in members)
    direct = any(row.get("rung") in {"DIRECT", "COUNTERPARTY"} for row in members)
    publishers = len(outlet_groups([str(row.get("publisher") or row.get("url") or "") for row in members]))
    return tier == 2 and linked and (publishers >= 2 or direct)


def event_register(pool: Pool, run_id: str, *, rows: Sequence[Mapping[str, Any]] | None = None, names: Mapping[str, str] | None = None,
                   escalating: Sequence[str] = ()) -> list[dict[str, Any]]:
    """The run's events, ▲ first, then =, then ▼. rows: the brief's routed rows (kept signals with their route), so a matched
    management request travels with its event. names: risk id -> name. escalating: the risk ids whose trend is Escalating."""
    from grit.core.priority import outlet_groups

    run = pool.run(run_id)
    rows = list(rows if rows is not None else pool.weekly_assessments(run_id))
    previous = pool.latest_run(run["pack"], before=run["run_date"], kind="READER_SCAN")
    before = _group(pool, pool.weekly_assessments(previous["run_id"])) if previous else {}
    names = dict(names or {})
    rising = set(escalating)
    events = []
    for event_id, members in _group(pool, rows).items():
        members = sorted(members, key=lambda row: str(row.get("published_at") or ""), reverse=True)
        # The best-sourced report leads (T1 first), then the briefing tier.
        lead = min(members, key=lambda row: (_band_rank(row), row.get("tier") or 9, -(row.get("corroboration") or 1), str(row.get("published_at") or "")))
        risks = list(dict.fromkeys(item for row in members for item in _strong_themes(row)))
        new = event_id not in before
        scores = [score for score in (significance_total(row) for row in members) if score is not None]
        elevated = _elevated(members)
        band = UP if elevated and (new or rising & set(risks)) else SAME
        dates = sorted(str(row.get("published_at") or "")[:10] for row in members if row.get("published_at"))
        events.append({
            "event_id": event_id, "band": band, "new": new, "score": max(scores) if scores else None,
            "elevated": band == UP or (elevated and (bool(scores) or min((row.get("tier") or 9) for row in members) == 1 or any(_live_red(row) for row in members))),
            "tier": min((row.get("tier") or 9) for row in members), "red": any(red_status(row) == "CONFIRMED" for row in members),
            "red_candidate": any(red_status(row) == "CANDIDATE" for row in members),
            "lead": lead, "members": members, "signal_ids": [row["signal_id"] for row in members],
            "publishers": len(outlet_groups([str(row.get("publisher") or row.get("url") or "") for row in members])),
            "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
            "risks": risks, "risk_label": risk_labels(risks, names),
            "requests": list(dict.fromkeys(str((row.get("route") or {}).get("request_id")) for row in members if (row.get("route") or {}).get("request_id"))),
            "previous_lead": (min(before[event_id], key=lambda row: (row.get("tier") or 9)) if not new else None),
        })
    for event_id, members in before.items():
        if event_id in {event["event_id"] for event in events} or not _elevated(members):
            continue
        lead = min(members, key=lambda row: (row.get("tier") or 9, str(row.get("published_at") or "")))
        risks = list(dict.fromkeys(item for row in members for item in _strong_themes(row)))
        events.append({
            "event_id": event_id, "band": DOWN, "new": False, "score": None, "elevated": False, "tier": lead.get("tier") or 9, "red": False, "red_candidate": False, "lead": lead, "members": [],
            "signal_ids": [], "publishers": 0, "first_date": None, "last_date": None, "risks": risks, "risk_label": risk_labels(risks, names),
            "requests": [], "previous_lead": lead,
        })
    events = _merge_stories(events, names, _places(run.get("pack")))
    order = {UP: 0, SAME: 1, DOWN: 2}
    rungs = {"DIRECT": 0, "COUNTERPARTY": 1}
    events.sort(key=lambda event: (order[event["band"]], not event["elevated"], not event["red"], -(event["score"] if event["score"] is not None else -1),
                                   not event["red_candidate"], event["tier"], rungs.get(str(event["lead"].get("rung")), 2),
                                   -event["publishers"], -len(event["members"]), str(event["lead"].get("title"))))
    return events


STORY_TERMS, STORY_DAYS, STORY_RISKS = 2, 7, 3


def _places(pack: str | None) -> dict[str, re.Pattern[str]]:
    """The pack's jurisdictions, each with a pattern for its name or a short form at the start of a word ("Israeli")."""
    if not pack:
        return {}
    from grit.core.red_alert import COUNTRY_ALIASES
    from grit.pipeline.reader_scan import _load_pack, jurisdictions

    try:
        countries = list(jurisdictions(_load_pack(pack)))
    except Exception:  # a pack without a typed graph has no jurisdictions to compare
        return {}
    return {country: re.compile(r"(?<![\w])(?:" + "|".join(re.escape(name) for name in (country, *COUNTRY_ALIASES.get(country, ()))) + r")", re.IGNORECASE)
            for country in countries}


def _merge_stories(events: list[dict[str, Any]], names: Mapping[str, str], places: Mapping[str, re.Pattern[str]] | None = None) -> list[dict[str, Any]]:
    """In a reasoned run, elevated events are one story (methodology: cluster the same incident once) when, within a week,
    their reports share two or more distinctive headline words, or name the same jurisdiction where the business has
    entities and share a risk. The merged event keeps the best-sourced lead, the top score and its most common risks."""
    from datetime import date

    from grit.core.headlines import distinctive_terms
    from grit.core.priority import outlet_groups

    merging = [event for event in events if event["elevated"] and event["members"] and event.get("score") is not None]
    if len(merging) < 2:
        return events
    titles = {event["event_id"]: " ".join(str(row.get("title") or "") for row in event["members"]) for event in merging}
    terms = {key: distinctive_terms(text) for key, text in titles.items()}
    # A jurisdiction counts when a headline names it. A page whose headline is too poor to compare (fewer than two distinctive
    # words) is read by its opening instead, so it still joins its story; body text never joins two well-titled stories.
    def place_text(row: Mapping[str, Any]) -> str:
        title = str(row.get("title") or "")
        return title if len(distinctive_terms(title)) >= STORY_TERMS else f"{title} {str(row.get('text') or '')[:300]}"

    leads = {event["event_id"]: " ".join(place_text(row) for row in event["members"]) for event in merging}
    named = {key: {country for country, pattern in (places or {}).items() if pattern.search(text)} for key, text in leads.items()}

    def near(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
        try:
            start = max(date.fromisoformat(str(first["first_date"])), date.fromisoformat(str(second["first_date"])))
            end = min(date.fromisoformat(str(first["last_date"])), date.fromisoformat(str(second["last_date"])))
        except (TypeError, ValueError):
            return True
        return (start - end).days <= STORY_DAYS

    # Each event joins the highest-scored story whose own reports it matches, never through a chain of weak matches.
    seeds: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for event in sorted(merging, key=lambda item: (-(item["score"] or 0), item["event_id"])):
        home = next((seed for seed in seeds if near(seed, event) and (
            len(terms[seed["event_id"]] & terms[event["event_id"]]) >= STORY_TERMS
            or (named[seed["event_id"]] & named[event["event_id"]] and set(seed["risks"]) & set(event["risks"])))), None)
        if home is None:
            seeds.append(event)
            groups[event["event_id"]] = [event]
        else:
            groups[home["event_id"]].append(event)
    merged = {}
    for seed_id, parts in groups.items():
        if len(parts) == 1:
            continue
        best = parts[0]
        members = [row for event in parts for row in event["members"]]
        dates = sorted(str(event[key]) for event in parts for key in ("first_date", "last_date") if event.get(key))
        merged[best["event_id"]] = {
            **best, "members": members, "signal_ids": [row["signal_id"] for row in members],
            # The best-sourced report that matches the story's own headline words leads; otherwise the seed's lead stands.
            "lead": min((row for row in members if row is best["lead"] or distinctive_terms(str(row.get("title") or "")) & terms[seed_id]),
                        key=lambda row: (_band_rank(row), row.get("tier") or 9)),
            "band": UP if any(event["band"] == UP for event in parts) else best["band"], "new": any(event["new"] for event in parts),
            "red": any(event["red"] for event in parts), "red_candidate": any(event["red_candidate"] for event in parts),
            "tier": min(event["tier"] for event in parts), "risks": _common_risks(parts),
            "publishers": len(outlet_groups([str(row.get("publisher") or row.get("url") or "") for row in members])),
            "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
            "requests": list(dict.fromkeys(item for event in parts for item in event["requests"])),
            "merged_events": [event["event_id"] for event in parts],
        }
        merged[best["event_id"]]["risk_label"] = risk_labels(merged[best["event_id"]]["risks"], names)
        for event in parts:
            if event["event_id"] != best["event_id"]:
                merged[event["event_id"]] = None
    return [merged.get(event["event_id"], event) if event["event_id"] in merged else event for event in events
            if merged.get(event["event_id"], event) is not None]


def _common_risks(parts: Sequence[Mapping[str, Any]]) -> list[str]:
    """The risks most of a merged story's reports carry, at most STORY_RISKS, so its label stays readable."""
    from collections import Counter

    counts: Counter[str] = Counter()
    for event in parts:
        for item in event["risks"]:
            counts[item] += len(event["members"]) or 1
    return [item for item, _ in counts.most_common(STORY_RISKS)]


def risk_names(pack: Any) -> dict[str, str]:
    from grit.pipeline.reader_scan import standing_items

    return {item["id"]: item["name"] for item in standing_items(pack)}


def draft_rows(pool: Pool, run: Any, *, client: Any, model: str, log: Callable[[str], None] = print) -> dict[str, Any]:
    """The EventAssessor drafts a row for each elevated event, up to MAX_ROWS. Rows are saved on the run, keyed by event id.
    A withheld row is logged to the QA log, and the brief shows the event with its lead report only."""
    from grit.agents import reader as agents
    from grit.daily.index import cluster_events
    from grit.pipeline.reader_scan import _load_pack, business_area_context, graph_pack, owner_roles

    try:
        cluster_events(pool)
    except Exception as exc:  # events need embeddings; without them each signal stands alone
        log(f"  Events: clustering skipped ({type(exc).__name__}).")
    pack = _load_pack(run.pack)
    names = risk_names(pack)
    rising = [row["condition_id"] for row in pool.conditions(run.run_id) if row.get("trend_label") == "Escalating"]
    events = [event for event in event_register(pool, run.run_id, names=names, escalating=rising) if event["elevated"]][:MAX_ROWS]
    area = business_area_context(pack)
    owners = owner_roles(pack)
    themes = {row["themeId"]: row for row in (graph_pack(pack).themes or {}).get("themes") or []}
    titles = {row["request_id"]: row["title"] for row in pool.management_requests()}
    saved: dict[str, Any] = {}
    counts = {"VALIDATED": 0, "WITHHELD": 0}
    for event in events:
        table, back = agents.source_table(event["members"][:MAX_SOURCES])
        risks = [{"label": risk_labels([item], names), "name": names.get(item, item), "drivers": (themes.get(item) or {}).get("sheetDetail")} for item in event["risks"][:4]]
        result, call = agents.draft_event_row(client, model, event_id=event["event_id"], business_area=area, owners=owners, risks=risks,
                                              requests=[titles.get(item, item) for item in event["requests"]], sources=table)
        pool.log(actor_type="AGENT", run_id=run.run_id, **call.as_record())
        counts[result["status"]] = counts.get(result["status"], 0) + 1
        if result["status"] == "VALIDATED":
            swap = lambda text: re.sub(r"\[(S\d+(?:\s*,\s*S\d+)*)\]", lambda m: "[" + ", ".join(back[ref.strip()] for ref in m.group(1).split(",") if ref.strip() in back) + "]", text)  # noqa: E731
            saved[event["event_id"]] = {
                "item": result["item"], "summary": swap(result["summary"]), "implications": swap(result["implications"]),
                "response": result["response"], "source_ids": [back[ref] for ref in result["source_ids"] if ref in back],
            }
        else:
            pool.add_qa(run.run_id, item=f"Event: {event['lead'].get('title')}", prior=None,
                        correction="Escalating-items row withheld: the draft failed its checks twice. " + "; ".join(result["violations"][:2]), origin="CHECK")
        log(f"  EventAssessor {event['event_id'][:20]:20} {result['status']}")
    pool.update_run_config(run.run_id, event_rows=saved)
    return {"events": len(events), **counts}
