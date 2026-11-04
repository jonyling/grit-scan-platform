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
    mapping = _event_ids(pool, [row["signal_id"] for row in rows])
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(mapping[row["signal_id"]], []).append(row)
    return grouped


def red_status(row: Mapping[str, Any]) -> str | None:
    """A rule raises a RED candidate. Only an R&A decision confirms or rejects its impact."""
    if not row.get("red"):
        return None
    return str(row.get("red_status") or "CANDIDATE")


def _live_red(row: Mapping[str, Any]) -> bool:
    return red_status(row) in {"CANDIDATE", "CONFIRMED"}


def _elevated(members: Sequence[Mapping[str, Any]]) -> bool:
    """Material: RED, tier 1, or tier 2 with a risk linkage and corroboration or a direct link."""
    from grit.core.priority import outlet_groups

    if not members:
        return False
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
        lead = min(members, key=lambda row: (row.get("tier") or 9, -(row.get("corroboration") or 1), str(row.get("published_at") or "")))
        risks = list(dict.fromkeys(item for row in members for item in _strong_themes(row)))
        new = event_id not in before
        elevated = _elevated(members)
        band = UP if elevated and (new or rising & set(risks)) else SAME
        dates = sorted(str(row.get("published_at") or "")[:10] for row in members if row.get("published_at"))
        events.append({
            "event_id": event_id, "band": band, "new": new, "elevated": band == UP or (elevated and (min((row.get("tier") or 9) for row in members) == 1 or any(_live_red(row) for row in members))),
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
            "event_id": event_id, "band": DOWN, "new": False, "elevated": False, "tier": lead.get("tier") or 9, "red": False, "red_candidate": False, "lead": lead, "members": [],
            "signal_ids": [], "publishers": 0, "first_date": None, "last_date": None, "risks": risks, "risk_label": risk_labels(risks, names),
            "requests": [], "previous_lead": lead,
        })
    order = {UP: 0, SAME: 1, DOWN: 2}
    rungs = {"DIRECT": 0, "COUNTERPARTY": 1}
    events.sort(key=lambda event: (order[event["band"]], not event["elevated"], not event["red"], not event["red_candidate"], event["tier"], rungs.get(str(event["lead"].get("rung")), 2),
                                   -event["publishers"], -len(event["members"]), str(event["lead"].get("title"))))
    return events


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
