"""The week's events: the week's kept signals grouped into events, each with its risk linkage and a band against the
previous report. Deterministic, apart from the rows, which the EventAssessor drafts and the checks decide.

A business-area brief (spec 49) puts each event in one table: Management Focus when it answers an active management
request, else Trends/Developments when it is competitor, sector or technology news, else Key, T2 & Emerging when it touches
a register risk, else Other Risk Themes when it touches a Group risk, else Trends/Developments. Each table holds at most MAX_SECTION_ROWS rows. The Group radar (spec 44) shows its elevated events.

| Band | Rule |
|---|---|
| ▲ | A material event that is new since the previous report, or that touches a risk whose trend this run is Escalating. |
| = | Any other event with a report this week. |
| ▼ | A material event of the previous report with no report this week. |

A material event is RED, or tier 1, or tier 2 with a risk linkage and either two or more independent publishers or a direct or
counterparty link to the business area. An elevated event is a ▲ event, or a continuing material event that is tier 1 or
RED. The Group radar's escalating-items table shows the elevated events, at most MAX_ROWS.

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
MAX_ROWS = 8  # escalating and elevated rows drafted per Group run
MAX_SECTION_ROWS = 10  # rows drafted per section of a business-area brief
MAX_SOURCES = 6  # reports per event shown to the EventAssessor
# The sections of a business-area brief, in reading order. Each event goes to the first section it qualifies for.
MANAGEMENT_FOCUS, REGISTER_RISK, OTHER_THEME, TREND = SECTIONS = ("MANAGEMENT_FOCUS", "REGISTER_RISK", "OTHER_THEME", "TREND")
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


def _group_risks(row: Mapping[str, Any]) -> list[str]:
    return [str(hit["name"]) for hit in row.get("risk_cascade") or [] if isinstance(hit, Mapping) and hit.get("level") == "GROUP" and hit.get("name")]


def _request_ids(row: Mapping[str, Any]) -> list[str]:
    """The management requests a signal answers: its reader route, or the keyword rule recorded at assessment."""
    found = [str((row.get("route") or {}).get("request_id"))] if (row.get("route") or {}).get("request_id") else []
    found += [str(hit["item_id"]) for hit in (row.get("link") or {}).get("reader_hits") or [] if isinstance(hit, Mapping) and hit.get("item_type") == "REQUEST"]
    return found


# A register risk with one of these names is about the market, not the business area's own exposure.
_MARKET_RISK = re.compile(r"competi|emerging technolog", re.IGNORECASE)
_EXPOSED, _MARKET = frozenset({"DIRECT", "COUNTERPARTY"}), frozenset({"COMPETITOR", "SECTOR"})


def is_trend(event: Mapping[str, Any], names: Mapping[str, str]) -> bool:
    """Competitor, sector and technology news: no member names the business area's own exposure, and a member is a
    competitor or sector report, or every risk the event touches is a market risk such as Competition."""
    rungs = {str(row.get("rung") or "") for row in event.get("members") or []}
    if rungs & _EXPOSED:
        return False
    risks = event.get("risks") or []
    return bool(rungs & _MARKET) or (bool(risks) and all(_MARKET_RISK.search(names.get(item, "")) for item in risks))


def section_of(event: Mapping[str, Any], active_requests: set[str], names: Mapping[str, str] | None = None) -> str:
    """Management Focus when it answers an active request, else a trend when it is competitor, sector or technology news,
    else a register risk, else a Group risk only, else a trend."""
    if active_requests & set(event.get("requests") or []):
        return MANAGEMENT_FOCUS
    if is_trend(event, names or {}):
        return TREND
    if event.get("risks"):
        return REGISTER_RISK
    if event.get("group_risks"):
        return OTHER_THEME
    return TREND


def section_events(events: Sequence[Mapping[str, Any]], active_requests: set[str], names: Mapping[str, str] | None = None) -> dict[str, list[Mapping[str, Any]]]:
    """This week's events per section, in event order, at most MAX_SECTION_ROWS each. Events without a report this week are left out."""
    found: dict[str, list[Mapping[str, Any]]] = {key: [] for key in SECTIONS}
    for event in events:
        if not event["members"]:
            continue
        rows = found[section_of(event, active_requests, names)]
        if len(rows) < MAX_SECTION_ROWS:
            rows.append(event)
    return found


def _strong_themes(row: Mapping[str, Any]) -> list[str]:
    found = [str(item.get("theme_id")) for item in row.get("themes") or [] if isinstance(item, Mapping) and item.get("strong") and item.get("theme_id")]
    return list(dict.fromkeys([*found, *(str(item) for item in row.get("condition_ids") or [])]))


def _event_ids(pool: Pool, signal_ids: Sequence[str]) -> dict[str, str]:
    if not signal_ids:
        return {}
    marks = ",".join("?" for _ in signal_ids)
    found = {row["signal_id"]: row["event_id"] for row in pool.rows(f"SELECT signal_id, event_id FROM event_members WHERE signal_id IN ({marks})", list(signal_ids))}
    return {signal_id: found.get(signal_id) or f"EVT-{signal_id[4:]}" for signal_id in signal_ids}


def same_relation_pairs(pool: Pool, run_id: str | None,
                        signal_ids: Sequence[str] | None = None) -> list[tuple[str, str]]:
    """Applied SAME signal pairs from the event_relations ledger (this run + cross-run; empty when flag off)."""
    if not run_id:
        return []
    from grit.core.event_relations import enabled, validated_same_pairs

    config = (pool.run(run_id) or {}).get("config") or {}
    if not enabled(config):
        return []
    rows = pool.event_relations_for_run(run_id, validated_only=True)
    if signal_ids:
        seen = {(row["left_signal_id"], row["right_signal_id"]) for row in rows}
        for row in pool.event_relations_for_signals(signal_ids, validated_only=True, labels=("SAME",)):
            pair = (row["left_signal_id"], row["right_signal_id"])
            if pair not in seen:
                rows.append(row)
                seen.add(pair)
    return validated_same_pairs(rows)


def _relations_on(pool: Pool, run_id: str | None) -> bool:
    from grit.core.event_relations import enabled

    if not run_id:
        return False
    return enabled((pool.run(run_id) or {}).get("config") or {})


def _group(pool: Pool, rows: Sequence[Mapping[str, Any]], *, run_id: str | None = None,
           same_pairs: Sequence[tuple[str, str]] | None = None,
           relations_on: bool | None = None,
           nominations: list[tuple[str, str]] | None = None) -> dict[str, list[Mapping[str, Any]]]:
    """Signals grouped by event cluster. Validated SAME relations dominate.

    When event_relations is on, citation word overlap only nominates pairs for the resolver; union still
    happens for validated SAME and for a shared curated alias/identifier. When off, a citation joins only
    with ≥2 shared non-place distinctive terms or a curated alias/identifier. Internal evidence never joins.
    """
    from grit.core.headlines import alias_ids, identifiers
    from grit.core.identity import filter_identity_eligible, non_place_terms, word_join_allowed

    rows = filter_identity_eligible(rows)
    mapping = _event_ids(pool, [row["signal_id"] for row in rows])
    parent = {event_id: event_id for event_id in mapping.values()}
    on = _relations_on(pool, run_id) if relations_on is None else bool(relations_on)

    def root(event_id: str) -> str:
        while parent[event_id] != event_id:
            parent[event_id] = parent[parent[event_id]]
            event_id = parent[event_id]
        return event_id

    def union(left: str, right: str) -> None:
        first, second = sorted((root(mapping[left]), root(mapping[right])))
        parent[second] = first

    def nominate(left: str, right: str) -> None:
        if nominations is None:
            return
        pair = (left, right) if left < right else (right, left)
        if pair not in nominations:
            nominations.append(pair)

    present = {row["signal_id"] for row in rows}
    for left, right in (same_pairs if same_pairs is not None else same_relation_pairs(pool, run_id, list(present))):
        if left in present and right in present and left in mapping and right in mapping:
            union(left, right)
    titles = {row["signal_id"]: str(row.get("title") or "") for row in rows}
    aliases = {row["signal_id"]: alias_ids(titles[row["signal_id"]]) for row in rows}
    idents = {row["signal_id"]: identifiers(titles[row["signal_id"]]) for row in rows}
    for row in rows:
        editorial = row.get("editorial") if isinstance(row.get("editorial"), Mapping) else {}
        for cited in editorial.get("source_ids") or []:
            if cited not in mapping or cited == row["signal_id"]:
                continue
            left, right = row["signal_id"], cited
            shared_alias = bool(aliases[left] & aliases.get(right, frozenset()))
            shared_id = bool(idents[left] & idents.get(right, frozenset()))
            if shared_alias or shared_id:
                union(left, right)
                continue
            if on:
                # Word overlap nominates only; never unions when relations are on.
                if non_place_terms(titles[left]) & non_place_terms(titles.get(right, "")):
                    nominate(left, right)
                continue
            if word_join_allowed(titles[left], titles.get(right, "")):
                union(left, right)
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


def _cited_reports(pool: Pool, run_id: str, rows: Sequence[Mapping[str, Any]], *,
                   relations_on: bool | None = None) -> dict[str, list[Mapping[str, Any]]]:
    """Reports that a reviewed selection cites as the same event but that are not in rows, such as the duplicates the review
    set aside, keyed by the citing signal. A citation counts only with a curated alias/identifier, or (flag-off) ≥2
    shared non-place distinctive terms. Internal evidence never enters the report list."""
    from grit.core.identity import is_identity_eligible, word_join_allowed
    from grit.core.headlines import alias_ids, identifiers

    present = {row["signal_id"] for row in rows}
    on = _relations_on(pool, run_id) if relations_on is None else bool(relations_on)

    def cited(row: Mapping[str, Any]) -> list[str]:
        editorial = row.get("editorial") if isinstance(row.get("editorial"), Mapping) else {}
        return [str(item) for item in editorial.get("source_ids") or [] if str(item) not in present]
    wanted = {item for row in rows for item in cited(row)}
    if not wanted:
        return {}
    try:
        reports = {row["signal_id"]: row for row in pool.weekly_assessments(run_id, include_excluded=True)
                   if row["signal_id"] in wanted and is_identity_eligible(row)}
    except ValueError:
        return {}
    found: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        title = str(row.get("title") or "")
        own_aliases, own_ids = alias_ids(title), identifiers(title)
        for item in cited(row):
            other = reports.get(item)
            if other is None:
                continue
            other_title = str(other.get("title") or "")
            if own_aliases & alias_ids(other_title) or own_ids & identifiers(other_title):
                found.setdefault(row["signal_id"], []).append(other)
            elif not on and word_join_allowed(title, other_title):
                found.setdefault(row["signal_id"], []).append(other)
    return found


def event_register(pool: Pool, run_id: str, *, rows: Sequence[Mapping[str, Any]] | None = None, names: Mapping[str, str] | None = None,
                   escalating: Sequence[str] = (), merges: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """The run's events, ▲ first, then =, then ▼. rows: the brief's routed rows (kept signals with their route), so a matched
    management request travels with its event. names: risk id -> name. escalating: the risk ids whose trend is Escalating.
    merges: event id -> the event whose row it joins, as label_merges recorded them when the rows were drafted.

    An event's reports are its members and the other reports of it that a member's selection cites, best band first. A
    cited report can lead and is listed, but the score, band, risks and story merging come from the members alone.
    Internal evidence is excluded from identity; oversized/multi-country merges are split back to pre-merge partitions.
    """
    from grit.core.identity import filter_identity_eligible, is_identity_suspect, split_suspect_to_premerge
    from grit.core.priority import outlet_groups

    run = pool.run(run_id)
    relations_on = _relations_on(pool, run_id)
    nominations: list[tuple[str, str]] = []
    rows = filter_identity_eligible(list(rows if rows is not None else pool.weekly_assessments(run_id)))
    cited = (_cited_reports(pool, run_id, rows, relations_on=relations_on)
             if (run.get("config") or {}).get("weekly_mode") == "reasoned" else {})
    previous = pool.latest_run(run["pack"], before=run["run_date"], kind="READER_SCAN")
    same_pairs = same_relation_pairs(pool, run_id)
    before = _group(pool, pool.weekly_assessments(previous["run_id"]), run_id=previous["run_id"]) if previous else {}
    names = dict(names or {})
    rising = set(escalating)
    events = []
    for event_id, members in _group(pool, rows, run_id=run_id, same_pairs=same_pairs,
                                    relations_on=relations_on, nominations=nominations).items():
        members = sorted(members, key=lambda row: str(row.get("published_at") or ""), reverse=True)
        own = {row["signal_id"] for row in members}
        extra = list({row["signal_id"]: row for member in members for row in cited.get(member["signal_id"], []) if row["signal_id"] not in own}.values())
        # The best-sourced report leads (T1 first), then the briefing tier.
        newest = sorted([*members, *extra], key=lambda row: str(row.get("published_at") or ""), reverse=True)
        reports = sorted(newest, key=lambda row: (_band_rank(row), row.get("tier") or 9))
        lead = _lead(members, extra)
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
            "lead": lead, "members": members, "reports": reports, "signal_ids": [row["signal_id"] for row in reports], "cited": [row["signal_id"] for row in extra],
            "publishers": len(outlet_groups([str(row.get("publisher") or row.get("url") or "") for row in reports])),
            "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
            "risks": risks, "risk_label": risk_labels(risks, names),
            "group_risks": list(dict.fromkeys(name for row in members for name in _group_risks(row))),
            "requests": list(dict.fromkeys(item for row in members for item in _request_ids(row))),
            "previous_lead": (min(before[event_id], key=lambda row: (row.get("tier") or 9)) if not new else None),
            "merged_events": [event_id],
        })
    for event_id, members in before.items():
        if event_id in {event["event_id"] for event in events} or not _elevated(members):
            continue
        lead = min(members, key=lambda row: (row.get("tier") or 9, str(row.get("published_at") or "")))
        risks = list(dict.fromkeys(item for row in members for item in _strong_themes(row)))
        events.append({
            "event_id": event_id, "band": DOWN, "new": False, "score": None, "elevated": False, "tier": lead.get("tier") or 9, "red": False, "red_candidate": False, "lead": lead, "members": [], "reports": [],
            "signal_ids": [], "publishers": 0, "first_date": None, "last_date": None, "risks": risks, "risk_label": risk_labels(risks, names),
            "group_risks": [], "requests": [], "previous_lead": lead,
        })
    pre_merge = {event["event_id"]: dict(event) for event in events if event.get("members")}
    events = _merge_stories(events, names, _places(run.get("pack")), same_pairs=same_pairs,
                            relations_on=relations_on, nominations=nominations)
    events = apply_merges(events, merges or {}, names, relations_on=relations_on, nominations=nominations)
    events = _apply_cluster_guard(events, pre_merge)
    if nominations:
        # Persist for resolve_event_relations / nightly reuse (word joins nominate; they do not union).
        existing = list((run.get("config") or {}).get("identity_nomination_pairs") or [])
        seen = {tuple(pair) for pair in existing if isinstance(pair, (list, tuple)) and len(pair) == 2}
        for pair in nominations:
            if pair not in seen:
                existing.append([pair[0], pair[1]])
                seen.add(pair)
        try:
            pool.update_run_config(run_id, identity_nomination_pairs=existing)
        except Exception:
            pass
    order = {UP: 0, SAME: 1, DOWN: 2}
    rungs = {"DIRECT": 0, "COUNTERPARTY": 1}
    events.sort(key=lambda event: (order[event["band"]], not event["elevated"], not event["red"], -(event["score"] if event["score"] is not None else -1),
                                   not event["red_candidate"], event["tier"], rungs.get(str(event["lead"].get("rung")), 2),
                                   -event["publishers"], -len(event["members"]), str(event["lead"].get("title"))))
    return events


def _apply_cluster_guard(events: list[dict[str, Any]],
                         pre_merge: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """P0.4: oversized or multi-country events are IDENTITY_SUSPECT and split to pre-merge partitions.

    RelationCritic today only rejects pairwise edges (SPLIT → INSUFFICIENT); it does not emit member
    partitions. Closest safe deterministic behaviour: restore pre-merge events and flag the split.
    """
    from grit.core.identity import IDENTITY_SUSPECT, is_identity_suspect, split_suspect_to_premerge

    out: list[dict[str, Any]] = []
    for event in events:
        if not event.get("members") or not is_identity_suspect(event):
            out.append(event)
            continue
        merged_ids = list(event.get("merged_events") or [event["event_id"]])
        parts = [pre_merge[eid] for eid in merged_ids if eid in pre_merge and pre_merge[eid].get("members")]
        # Deduplicate while preserving order.
        seen: set[str] = set()
        unique_parts = []
        for part in parts:
            eid = str(part["event_id"])
            if eid not in seen:
                seen.add(eid)
                unique_parts.append(part)
        split = split_suspect_to_premerge(event, unique_parts if len(unique_parts) >= 2 else None)
        for row in split:
            row["identity_status"] = IDENTITY_SUSPECT
            out.append(row)
    return out


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


def _lead(members: Sequence[Mapping[str, Any]], cited: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """The best-sourced member (T1 first), then the briefing tier. A cited report leads only with a strictly better band."""
    def rank(row: Mapping[str, Any]) -> tuple[Any, ...]:
        return (_band_rank(row), row.get("tier") or 9, -(row.get("corroboration") or 1), str(row.get("published_at") or ""))

    lead = min(members, key=rank)
    better = [row for row in cited if _band_rank(row) < _band_rank(lead)]
    return min(better, key=rank) if better else lead


def _near(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    """The two events' reports fall within STORY_DAYS of each other."""
    from datetime import date

    try:
        start = max(date.fromisoformat(str(first["first_date"])), date.fromisoformat(str(second["first_date"])))
        end = min(date.fromisoformat(str(first["last_date"])), date.fromisoformat(str(second["last_date"])))
    except (TypeError, ValueError):
        return True
    return (start - end).days <= STORY_DAYS


def _combine(parts: Sequence[dict[str, Any]], names: Mapping[str, str], words: frozenset[str]) -> dict[str, Any]:
    """One story from its events, the first being the seed. words: the story's own headline words."""
    from grit.core.headlines import distinctive_terms
    from grit.core.priority import outlet_groups

    best = parts[0]
    members = [row for event in parts for row in event["members"]]
    reports = sorted({row["signal_id"]: row for event in parts for row in event.get("reports") or event["members"]}.values(),
                     key=lambda row: (_band_rank(row), row.get("tier") or 9))
    own = {row["signal_id"] for row in members}
    matching = [row for row in members if row is best["lead"] or distinctive_terms(str(row.get("title") or "")) & words] or [best["lead"]]
    dates = sorted(str(event[key]) for event in parts for key in ("first_date", "last_date") if event.get(key))
    risks = _common_risks(parts)
    return {
        **best, "members": members, "reports": reports, "signal_ids": [row["signal_id"] for row in reports],
        # The best-sourced member that matches the story's own headline words leads, or a cited report with a better band.
        "lead": _lead(matching, [row for row in reports if row["signal_id"] not in own]),
        "band": UP if any(event["band"] == UP for event in parts) else best["band"], "new": any(event["new"] for event in parts),
        "red": any(event["red"] for event in parts), "red_candidate": any(event["red_candidate"] for event in parts),
        "tier": min(event["tier"] for event in parts), "risks": risks, "risk_label": risk_labels(risks, names),
        "publishers": len(outlet_groups([str(row.get("publisher") or row.get("url") or "") for row in reports])),
        "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
        "requests": list(dict.fromkeys(item for event in parts for item in event["requests"])),
        "group_risks": list(dict.fromkeys(item for event in parts for item in event.get("group_risks") or [])),
        "merged_events": [event_id for event in parts for event_id in event.get("merged_events") or [event["event_id"]]],
        "cited": [item for event in parts for item in event.get("cited") or []],
    }


def _replace(events: list[dict[str, Any]], groups: Mapping[str, Sequence[dict[str, Any]]], names: Mapping[str, str],
             words: Mapping[str, frozenset[str]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any] | None] = {}
    for seed_id, parts in groups.items():
        if len(parts) < 2:
            continue
        merged[seed_id] = _combine(parts, names, words.get(seed_id, frozenset()))
        for event in parts[1:]:
            merged[event["event_id"]] = None
    return [merged.get(event["event_id"], event) for event in events if merged.get(event["event_id"], event) is not None]


def _members_same(first: Mapping[str, Any], second: Mapping[str, Any],
                  same_pairs: Sequence[tuple[str, str]] | None) -> bool:
    """True when any member of first is SAME-linked to any member of second."""
    if not same_pairs:
        return False
    left = {row["signal_id"] for row in first.get("members") or []}
    right = {row["signal_id"] for row in second.get("members") or []}
    linked = set(same_pairs)
    return any(((a, b) if a < b else (b, a)) in linked for a in left for b in right if a != b)


def _merge_stories(events: list[dict[str, Any]], names: Mapping[str, str], places: Mapping[str, re.Pattern[str]] | None = None,
                   same_pairs: Sequence[tuple[str, str]] | None = None, *,
                   relations_on: bool = False,
                   nominations: list[tuple[str, str]] | None = None,
                   vectors: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """In a reasoned run, elevated events are one story when SAME links their anchors, or (flag-off) when anchors share
    ≥2 non-place distinctive terms or a curated alias/identifier within a week. Term bags come from anchors only
    (lead + ≤2 medoids). When relations are on, word overlaps nominate only; alias/identifier and SAME still union.
    places is unused (country alone never joins)."""
    from grit.core.headlines import alias_ids, distinctive_terms, identifiers
    from grit.core.identity import (
        anchor_members,
        anchor_text,
        anchors_match,
        curated_overlap,
        place_tokens,
    )

    merging = [event for event in events if event["elevated"] and event["members"] and event.get("score") is not None]
    if len(merging) < 2:
        return events

    def nominate_events(left: Mapping[str, Any], right: Mapping[str, Any]) -> None:
        if nominations is None:
            return
        for a in anchor_members(left, vectors=vectors):
            for b in anchor_members(right, vectors=vectors):
                aid, bid = str(a.get("signal_id") or ""), str(b.get("signal_id") or "")
                if not aid or not bid or aid == bid:
                    continue
                pair = (aid, bid) if aid < bid else (bid, aid)
                if pair not in nominations:
                    nominations.append(pair)

    # P0.5: vocabulary from anchor titles only, never the union of all members.
    titles = {event["event_id"]: anchor_text(event, vectors=vectors) for event in merging}
    terms = {key: distinctive_terms(text) for key, text in titles.items()}
    aliases = {key: alias_ids(text) for key, text in titles.items()}
    idents = {key: identifiers(text) for key, text in titles.items()}

    def place_text(row: Mapping[str, Any]) -> str:
        title = str(row.get("title") or "")
        return title if len(distinctive_terms(title)) >= STORY_TERMS else f"{title} {str(row.get('text') or '')[:300]}"

    for event in merging:
        if len(terms[event["event_id"]] - place_tokens()) < STORY_TERMS:
            text = " ".join(place_text(row) for row in anchor_members(event, vectors=vectors))
            titles[event["event_id"]] = text
            terms[event["event_id"]] = distinctive_terms(text)
            aliases[event["event_id"]] = alias_ids(text)
            idents[event["event_id"]] = identifiers(text)

    def anchors_same(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
        from grit.core.identity import anchor_signal_ids

        if not same_pairs:
            return False
        left = anchor_signal_ids(first, vectors=vectors)
        right = anchor_signal_ids(second, vectors=vectors)
        linked = set(same_pairs)
        return any(((a, b) if a < b else (b, a)) in linked for a in left for b in right if a != b)

    seeds: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for event in sorted(merging, key=lambda item: (-(item["score"] or 0), item["event_id"])):
        home = None
        for seed in seeds:
            if not _near(seed, event):
                continue
            # Anchor-to-anchor curated alias / identifier always unions (Plus Ultra).
            alias_hit = bool(aliases[seed["event_id"]] & aliases[event["event_id"]]
                             or idents[seed["event_id"]] & idents[event["event_id"]]
                             or curated_overlap(titles[seed["event_id"]], titles[event["event_id"]]))
            same_hit = anchors_same(seed, event)
            non_place = (terms[seed["event_id"]] & terms[event["event_id"]]) - place_tokens()
            word_hit = len(non_place) >= STORY_TERMS and anchors_match(
                seed, event, same_pairs=same_pairs, vectors=vectors, min_terms=STORY_TERMS)
            if same_hit or alias_hit:
                home = seed
                break
            if relations_on:
                if word_hit or non_place:
                    nominate_events(seed, event)
                continue
            if word_hit:
                home = seed
                break
        if home is None:
            seeds.append(event)
            groups[event["event_id"]] = [event]
        else:
            # Log lead changes; _combine recomputes the lead from matching members.
            groups[home["event_id"]].append(event)
            if home.get("lead", {}).get("signal_id") != event.get("lead", {}).get("signal_id"):
                home.setdefault("lead_changes", []).append({
                    "from": home.get("lead", {}).get("signal_id"),
                    "candidate": event.get("lead", {}).get("signal_id"),
                    "merged_event": event["event_id"],
                })
    seed_terms = {seed_id: terms.get(seed_id, frozenset()) for seed_id in groups}
    return _replace(events, groups, names, seed_terms)


def label_merges(events: Sequence[Mapping[str, Any]], rows: Mapping[str, Mapping[str, Any]],
                 same_pairs: Sequence[tuple[str, str]] | None = None, *,
                 relations_on: bool = False,
                 nominations: list[tuple[str, str]] | None = None) -> dict[str, str]:
    """Join drafted events by SAME / curated alias-id first, then by label words (flag-off only).

    When relations are on, word overlap nominates only. Place tokens never count toward the word threshold.
    events: in report order. rows: event id -> drafted row. Returns event id -> the event it joins, with no chains."""
    from grit.core.headlines import alias_ids, distinctive_terms, identifiers
    from grit.core.identity import place_tokens

    seeds: list[tuple[Mapping[str, Any], frozenset[str], frozenset[str], frozenset[str]]] = []
    joins: dict[str, str] = {}
    for event in events:
        row = rows.get(event["event_id"])
        if not row or not row.get("item"):
            continue
        item = str(row["item"])
        words = distinctive_terms(item) - place_tokens()
        names, aliases = identifiers(item), alias_ids(item)
        home = None
        for seed, seed_words, seed_names, seed_aliases in seeds:
            if not _near(seed, event):
                continue
            same_hit = _members_same(seed, event, same_pairs)
            alias_hit = bool(seed_names & names or seed_aliases & aliases)
            word_hit = len(seed_words & words) >= STORY_TERMS
            if same_hit or alias_hit:
                home = seed
                break
            if relations_on:
                if word_hit and nominations is not None:
                    for a in seed.get("members") or []:
                        for b in event.get("members") or []:
                            aid, bid = str(a.get("signal_id") or ""), str(b.get("signal_id") or "")
                            if aid and bid and aid != bid:
                                pair = (aid, bid) if aid < bid else (bid, aid)
                                if pair not in nominations:
                                    nominations.append(pair)
                continue
            if word_hit:
                home = seed
                break
        if home is None:
            seeds.append((event, words, names, aliases))
        else:
            joins[event["event_id"]] = home["event_id"]
    return joins


def apply_merges(events: list[dict[str, Any]], merges: Mapping[str, str], names: Mapping[str, str], *,
                 relations_on: bool = False,
                 nominations: list[tuple[str, str]] | None = None) -> list[dict[str, Any]]:
    """Recorded label merges (label_merges), applied where both events are present with a report this week.

    Vocabulary for the combined story lead comes from anchors only (P0.5)."""
    from grit.core.identity import anchor_terms

    present = {event["event_id"]: event for event in events if event["members"]}
    groups: dict[str, list[dict[str, Any]]] = {}
    for event_id, seed_id in merges.items():
        if event_id in present and seed_id in present and event_id != seed_id:
            groups.setdefault(seed_id, [present[seed_id]]).append(present[event_id])
    words = {seed_id: anchor_terms(present[seed_id]) for seed_id in groups}
    return _replace(events, groups, names, words)


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


def active_requests(pool: Pool, pack: str) -> set[str]:
    from grit.core.routing import request_in_pack

    return {str(row["request_id"]) for row in pool.management_requests(active_only=True) if request_in_pack(row, pack)}


def draft_rows(pool: Pool, run: Any, *, client: Any, model: str, log: Callable[[str], None] = print) -> dict[str, Any]:
    """The EventAssessor drafts the rows. A business area gets one row per event in each section, at most MAX_SECTION_ROWS
    a section. The Group radar gets its elevated events, at most MAX_ROWS. Rows are saved on the run, keyed by event id,
    with their section. A withheld row is logged to the QA log and left out of the brief."""
    from grit.agents import reader as agents
    from grit.daily.index import cluster_events
    from grit.pipeline.reader_scan import _load_pack, business_area_context, graph_pack, owner_roles
    from grit.report.brief import out_of_scope

    try:
        cluster_events(pool)
    except Exception as exc:  # events need embeddings, and without them each signal stands alone
        log(f"  Events: clustering skipped ({type(exc).__name__}).")
    pack = _load_pack(run.pack)
    names = risk_names(pack)
    titles = {row["request_id"]: row["title"] for row in pool.management_requests()}
    if pack.level not in {"BA", "ENTITY"}:
        rising = [row["condition_id"] for row in pool.conditions(run.run_id) if row.get("trend_label") == "Escalating"]
        jobs: list[tuple[str | None, Mapping[str, Any]]] = [
            (None, event) for event in [event for event in event_register(pool, run.run_id, names=names, escalating=rising) if event["elevated"]][:MAX_ROWS]]
    else:
        off_scope = out_of_scope(run.pack)
        rows = [row for row in pool.weekly_assessments(run.run_id)
                if (row.get("editorial") or {}).get("decision", "INCLUDE") in {"INCLUDE", "WATCH"}
                and (row.get("red") or not off_scope or not off_scope.search(str(row.get("title") or "")))]
        active = active_requests(pool, run.pack)
        sections = section_events(event_register(pool, run.run_id, rows=rows, names=names), active, names)
        jobs = [(section, event) for section in SECTIONS for event in sections[section]]
    events = [event for _section, event in jobs]
    area = business_area_context(pack)
    owners = owner_roles(pack)
    themes = {row["themeId"]: row for row in (graph_pack(pack).themes or {}).get("themes") or []}
    saved: dict[str, Any] = {}
    counts = {"VALIDATED": 0, "WITHHELD": 0}
    for section, event in jobs:
        table, back = agents.source_table((event.get("reports") or event["members"])[:MAX_SOURCES])
        risks = [{"label": risk_labels([item], names), "name": names.get(item, item), "drivers": (themes.get(item) or {}).get("sheetDetail")} for item in event["risks"][:4]]
        if section == OTHER_THEME:
            risks = [{"label": f"Group: {name}", "name": name} for name in event["group_risks"][:4]]
        result, call = agents.draft_event_row(client, model, event_id=event["event_id"], business_area=area, owners=owners, risks=risks,
                                              requests=[titles.get(item, item) for item in event["requests"] if section != OTHER_THEME], sources=table, section=section)
        pool.log(actor_type="AGENT", run_id=run.run_id, **call.as_record())
        counts[result["status"]] = counts.get(result["status"], 0) + 1
        if result["status"] == "VALIDATED":
            swap = lambda text: re.sub(r"\[(S\d+(?:\s*,\s*S\d+)*)\]", lambda m: "[" + ", ".join(back[ref.strip()] for ref in m.group(1).split(",") if ref.strip() in back) + "]", text)  # noqa: E731
            sequence = [
                {"date": step["date"], "text": swap(step["text"]), "source_id": back.get(step["source_id"], step["source_id"])}
                for step in result.get("sequence") or [] if isinstance(step, Mapping)
            ]
            implications = swap(result["implications"])
            saved[event["event_id"]] = {
                "item": result["item"], "summary": swap(result["summary"]), "sequence": sequence, "response": result["response"],
                # The Group radar's Implications column leads with the dated steps. A business-area row shows them under its summary.
                "implications": agents.format_timeline(sequence, implications) if section is None else implications,
                "opportunity": swap(result["opportunity"]) if result.get("opportunity") else None, "section": section,
                "source_ids": [back[ref] for ref in result["source_ids"] if ref in back],
            }
        else:
            pool.add_qa(run.run_id, item=f"Event: {event['lead'].get('title')}", prior=None,
                        correction="Event row withheld: the draft failed its checks twice. " + "; ".join(result["violations"][:2]),
                        source_ids=list(event["signal_ids"]), origin="CHECK")
        log(f"  EventAssessor {str(section or 'GROUP')[:16]:16} {event['event_id'][:20]:20} {result['status']}")
    withheld = [event["event_id"] for event in events if event["event_id"] not in saved]
    nominations: list[tuple[str, str]] = []
    merges = label_merges(
        events, saved, same_pairs=same_relation_pairs(pool, run.run_id),
        relations_on=_relations_on(pool, run.run_id), nominations=nominations,
    )
    if merges:
        log(f"  Story merging by item label: {len(merges)} row(s) join another event's row.")
    updates: dict[str, Any] = {"event_rows": saved, "event_rows_withheld": withheld, "event_merges": merges}
    if nominations:
        existing = list((run.config if hasattr(run, "config") else (pool.run(run.run_id) or {}).get("config") or {}).get("identity_nomination_pairs") or [])
        seen = {tuple(pair) for pair in existing if isinstance(pair, (list, tuple)) and len(pair) == 2}
        for pair in nominations:
            if pair not in seen:
                existing.append([pair[0], pair[1]])
                seen.add(pair)
        updates["identity_nomination_pairs"] = existing
    pool.update_run_config(run.run_id, **updates)
    return {"events": len(events), **counts}
