"""The brief model: one reader's weekly brief as plain data, built from the pool. Renderers turn it into Word, PowerPoint,
Excel, or Markdown. Every format says the same thing.

The model applies the reader's routing (spec 24 aperture and spec 29 priority) and reader profile (spec 39) to the shared
signals. The profile orders signals within a priority tier and hides none. It never changes a shared fact. Citations stay as signal ids until a renderer prints the outlet, the date, and the link.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

from grit.core.canonical import canonical_hash
from grit.core.corrections import ledger_view
from grit.core.routing import aperture_routing
from grit.ontology.pack import load_ontology_pack
from grit.pool import Pool
from grit.profiles import Profile
from grit.profiles.emphasis import profile_effect
from grit.profiles.memory import build_memory
from grit.settings import CONFIG_DIR

CITE = re.compile(r"\[((?:SIG-[0-9a-f]{16})(?:\s*,\s*SIG-[0-9a-f]{16})*)\]")
STATUS = "DRAFT_FOR_REVIEW"
PROJECT_TIER_LABEL = {"TIER_1_ACTIVE": "Tier-1 active", "PIPELINE": "Pipeline"}


def fmt_date(value: str | None) -> str | None:
    for pattern in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(str(value or ""), pattern)
            return f"{parsed.day} {parsed.strftime('%b %Y')}"
        except ValueError:
            continue
    return None


def fmt_when(value: str | None) -> str | None:
    """The publication date, with the time when the source states one: '3 Oct 2026, 08:15 UTC'."""
    try:
        parsed = datetime.strptime(str(value or ""), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return fmt_date(value)
    day = f"{parsed.day} {parsed.strftime('%b %Y')}"
    return day if (parsed.hour, parsed.minute, parsed.second) == (0, 0, 0) else f"{day}, {parsed.strftime('%H:%M')} UTC"


def text_only(runs: Sequence[Mapping[str, Any]]) -> str:
    """Runs as one line of text with the citations left out, for a table whose source column lists them."""
    return re.sub(r"\s+([.,;])", r"\1", re.sub(r"\s+", " ", "".join(run["text"] for run in runs if "text" in run))).strip()


def window_label(run_date: date, days: int) -> str:
    """The window the keep rule uses: the run date and the days - 1 days before it."""
    start = run_date - timedelta(days=days - 1)
    if start.year == run_date.year:
        return f"{start.day} {start.strftime('%B')} – {run_date.day} {run_date.strftime('%B %Y')}"
    return f"{start.day} {start.strftime('%B %Y')} – {run_date.day} {run_date.strftime('%B %Y')}"


def citations(text: str | None, signals: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str | None]]]:
    """Split text at its citation marks. Returns runs [{text}|{cite:[...]}] and the distinct sources cited."""
    runs: list[dict[str, Any]] = []
    cited: dict[str, dict[str, str | None]] = {}
    last = 0
    for match in CITE.finditer(text or ""):
        if match.start() > last:
            runs.append({"text": text[last : match.start()]})
        refs = []
        for signal_id in (part.strip() for part in match.group(1).split(",")):
            signal = signals.get(signal_id)
            if signal is None:
                continue
            ref = {
                "signal_id": signal_id, "outlet": signal.get("publisher") or "source", "date": fmt_date(signal.get("published_at")),
                "when": fmt_when(signal.get("published_at")), "url": signal.get("url"), "title": signal.get("title"),
            }
            stated = [fmt_date(day) for day in signal.get("event_dates") or [] if day != str(signal.get("published_at") or "")[:10]]
            if stated and ref.get("when"):
                ref["when"] = f"{ref['when']}; event dated {', '.join(stated)}"
            scope_label = {"HINDSIGHT": "hindsight", "EARLIER_CONTEXT": "earlier context"}.get(signal.get("time_scope"))
            if scope_label:
                for key in ("date", "when"):
                    if ref.get(key):
                        ref[key] = f"{ref[key]} ({scope_label})"
            refs.append(ref)
            cited[signal_id] = ref
        if refs:
            runs.append({"cite": refs})
        last = match.end()
    if last < len(text or ""):
        runs.append({"text": text[last:]})
    return runs, list(cited.values())


def plain(runs: Sequence[Mapping[str, Any]]) -> str:
    """Runs as one line of text with citations in the reference style: (Outlet, 1 Oct 2026)."""
    parts = []
    for run in runs:
        if "text" in run:
            parts.append(run["text"])
        else:
            parts.append("(" + "; ".join(f"{ref['outlet']}, {ref['date']}" if ref.get("date") else str(ref["outlet"]) for ref in run["cite"]) + ")")
    return re.sub(r"\s+([.,;])", r"\1", re.sub(r"\s+", " ", "".join(parts))).strip()


def _signals(pool: Pool, ids: set[str]) -> dict[str, dict[str, Any]]:
    found = {}
    for signal_id in ids:
        signal = pool.signal(signal_id)
        if signal:
            found[signal_id] = signal
    return found


def _cited_ids(*texts: str | None) -> set[str]:
    found: set[str] = set()
    for text in texts:
        for group in CITE.findall(text or ""):
            found.update(part.strip() for part in group.split(","))
    return found


def routed_signals(pool: Pool, run_id: str, profile: Profile) -> list[dict[str, Any]]:
    """Every kept signal with this reader's route and profile effect. Signals outside the aperture stay in the list with the
    reason. The profile only reorders."""
    rows = pool.weekly_assessments(run_id, include_excluded=True)
    memory = None
    if profile.signal_buttons:
        feedback = pool.feedback(profile.profile_id)
        memory = build_memory(profile.profile_id, feedback, pool.latest_assessments(row["signal_id"] for row in feedback))
    requests = pool.management_requests(active_only=True)
    run = pool.run(run_id) or {}
    decisions = pool.red_decisions(str(run.get("pack") or "")) if any(row.get("red") for row in rows) else {}
    out = []
    for row in rows:
        if row.get("red"):
            decision = decisions.get(row["signal_id"])
            row = {**row, "red_status": decision["decision"] if decision else "CANDIDATE", "red_decision": decision}
        route = aperture_routing(row, aperture=profile.aperture, packs=profile.packs, requests=requests)
        effect = profile_effect(row, profile, protected=bool(row.get("red")) or route.priority == "P1", memory=memory)
        selected = (row.get("editorial") or {}).get("decision", "INCLUDE") in {"INCLUDE", "WATCH"}
        out.append({**row, "route": route.as_record(), "preference": effect.as_record(), "shown": route.include and selected})
    out.sort(key=lambda row: str(row.get("published_at") or ""), reverse=True)
    out.sort(key=lambda row: (not row["shown"], row["route"]["priority"] or "P9", row.get("tier") or 9, not row.get("condition_ids"), -row["preference"]["affinity"], -(row.get("corroboration") or 1)))
    return out


def build_brief(pool: Pool, run_id: str, profile: Profile) -> dict[str, Any]:
    run = pool.run(run_id)
    if run is None:
        raise KeyError(f"No run {run_id} in the pool.")
    pack_name = run["pack"]
    brief_config = profile.brief_for(pack_name)
    if brief_config is None:
        raise ValueError(f"{profile.label} receives no brief for the {pack_name} pack.")
    pack = load_ontology_pack(CONFIG_DIR, pack_name)
    plan_config = pack.scan_plan or {}
    run_date = date.fromisoformat(run["run_date"])
    from grit.pipeline.reader_scan import scan_window

    window = scan_window(pack)
    previous = pool.latest_run(pack_name, before=run["run_date"], kind="READER_SCAN")

    from grit.pipeline.reader_scan import standing_items

    register_order = {item["id"]: number for number, item in enumerate(standing_items(pack))}
    conditions = sorted(pool.conditions(run_id), key=lambda row: (register_order.get(row["condition_id"], 999), row["condition_id"]))
    checks = pool.exposure_checks(run_id)
    watchpoints = pool.watchpoints(pack_name, open_only=False)
    updates = {row["watchpoint_id"]: row for row in pool.watchpoint_updates(run_id)}
    qa = pool.qa_log(run_id)
    routed = routed_signals(pool, run_id, profile)
    cards = {row["signal_id"]: row for row in pool.findings(run_id)}

    ids = _cited_ids(*(row.get("summary") for row in conditions), *(row.get("implication") for row in conditions), *(row.get("opportunity") for row in conditions), *(row.get("summary") for row in checks))
    ids |= {signal_id for row in conditions for option in row.get("options") or [] for signal_id in option.get("source_ids") or []}
    ids |= {signal_id for row in checks for signal_id in row.get("signal_ids") or []}
    ids |= {signal_id for row in conditions for signal_id in row.get("source_ids") or []}
    ids |= {signal_id for row in qa for signal_id in row.get("source_ids") or []}
    ids |= {signal_id for row in updates.values() for signal_id in row.get("signal_ids") or []}
    signals = _signals(pool, ids)

    def render(text: str | None) -> dict[str, Any]:
        runs, cited = citations(text, signals)
        return {"runs": runs, "plain": plain(runs), "sources": cited}

    projects_by_id = {row["projectId"]: row for row in pack.exposure.get("projects") or []}
    entity_lobs = {row["entityId"]: (row.get("lobGrouping") or "Other", row.get("lob")) for row in pack.exposure.get("entities") or []}
    project_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    entity_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    suppliers, customers, hazards, competitors = [], [], [], []
    for row in checks:
        entry = {"item_id": row["item_id"], "name": row["item_name"], "status": row["status"], "text": render(row["summary"]) if row["summary"] else None}
        if row["item_type"] == "PROJECT":
            project = projects_by_id.get(row["item_id"], {})
            entry["tier"] = PROJECT_TIER_LABEL.get(project.get("tier"), project.get("tier"))
            entry["note"] = project.get("scopeNote")
            project_groups[project.get("city") or "Other"].append(entry)
        elif row["item_type"] == "SUPPLIER":
            suppliers.append(entry)
        elif row["item_type"] == "CUSTOMER":
            customers.append(entry)
        elif row["item_type"] == "HAZARD":
            hazards.append(entry)
        elif row["item_type"] == "ENTITY":
            grouping, lob = entity_lobs.get(row["item_id"], (f"{pack.label} and the Group", None))
            entry["lob"] = lob
            entity_groups[grouping].append(entry)
        elif row["item_type"] == "COMPETITOR":
            competitors.append(entry)
    order = {row["projectId"]: number for number, row in enumerate(pack.exposure.get("projects") or [])}
    for items in project_groups.values():
        items.sort(key=lambda item: order.get(item["item_id"], 99))
    groups = []
    for city, items in sorted(project_groups.items(), key=lambda pair: min(order.get(item["item_id"], 99) for item in pair[1])):
        names = [re.sub(rf"^{re.escape(city)}\s+", "", item["name"]) for item in items]
        groups.append({"heading": f"{city}: {', '.join(names)}", "items": items})

    entities = [{"heading": grouping, "items": items} for grouping, items in sorted(entity_groups.items(), key=lambda pair: (pair[0].startswith(pack.label), pair[0]))]

    sections = set(brief_config.get("sections") or [])
    reader_priority = {row["signal_id"]: row["route"]["priority"] for row in routed if row["route"].get("priority")}
    # A Group condition names the business areas it touches (spec 37). The scan plan proposes them by pack id.
    from grit.ontology.pack import manifests

    labels = {name: manifest.get("label") or name for name, manifest in manifests(CONFIG_DIR).items()}
    areas = {row["conditionId"]: [labels.get(item, item) for item in row.get("businessAreas") or []] for row in plan_config.get("conditions") or []}
    group = pack.level == "GROUP"
    condition_rows = []
    for number, row in enumerate(conditions, start=1):
        ranking = row.get("ranking") or {}
        basis = render(row["summary"]) if row["summary"] else None
        implication = render(row["implication"]) if row.get("implication") else None
        opportunity = render(row["opportunity"]) if row.get("opportunity") else None
        sources = list({ref["signal_id"]: ref for ref in [*(basis or {}).get("sources", []), *(implication or {}).get("sources", []), *(opportunity or {}).get("sources", [])]}.values())
        condition_rows.append({
            "number": number, "condition_id": row["condition_id"], "name": row["condition_name"], "group": ranking.get("domain") if ranking.get("kind") == "THEME" else None,
            "covered_by": ranking.get("coveredBy"), "trend": row["trend_label"], "confidence": (row["confidence"] or "").title() or "Not established",
            "status": row["status"], "basis": basis or {"runs": [{"text": "Withheld: the draft failed its checks. See the QA log."}], "plain": "Withheld: the draft failed its checks. See the QA log.", "sources": []},
            "development": text_only(basis["runs"]) if basis else "Withheld: the draft failed its checks. See the QA log.", "sources": sources,
            "priority": min((reader_priority[ref["signal_id"]] for ref in sources if ref["signal_id"] in reader_priority), default=None),
            "themes": ranking.get("steThemes") or [], "risk_level": ranking.get("riskLevel"), "priority_label": ranking.get("priorityLabel"),
            "tier": ranking.get("tier"),
            # Spec 36: a key risk, or any escalating theme, gets a full entry. Every other theme stays one table row. Every Group
            # condition is a row of the radar (spec 37).
            "full": group or (ranking.get("kind") == "THEME" and (ranking.get("tier") == "KEY" or row.get("movement") == "ESCALATING")),
            "opportunity": opportunity, "counter_indicator": row.get("counter_indicator"), "radar": group,
            "business_areas": areas.get(row["condition_id"], []),
            "implication": implication,
            "options": [
                {**{key: option.get(key) for key in ("kind", "owner", "builds_on", "trigger")},
                 "source_ids": [str(item) for item in option.get("source_ids") or []],
                 "text": render(option["option"] + (f" [{', '.join(option['source_ids'])}]" if option.get("source_ids") else ""))}
                for option in row.get("options") or []
            ] if "RESPONSE_OPTIONS" in sections else [],
        })

    shown = [row for row in routed if row["shown"]]
    max_findings = int(brief_config.get("maxFindings") or 8)
    # The package holds what the reader receives, so a section the profile leaves out adds no member.
    priority_rows = [row for row in shown if (row.get("tier") or 9) <= 2][:max_findings] if "PRIORITY_FINDINGS" in sections else []
    findings = []
    for row in priority_rows:
        card = (cards.get(row["signal_id"]) or {}).get("card")
        findings.append({
            "signal_id": row["signal_id"], "title": row["title"], "outlet": row["publisher"], "date": fmt_date(row["published_at"]), "url": row["url"],
            "priority": row["route"]["priority"], "tier": row.get("tier"), "rung": row.get("rung"), "why": row["route"]["matched"][:2] or [row.get("relevance_basis")],
            "risks": [f"{hit['level'].title()}: {hit['name']}" for hit in (row.get("risk_cascade") or [])[:3]],
            "corroboration": row.get("corroboration"), "red": row.get("red_status") == "CONFIRMED", "red_status": row.get("red_status"), "harm": _harm_line(row), "preference": row["preference"]["reasons"],
            "card": _card_summary(card) if card else None,
        })
    others = [row for row in shown if row not in priority_rows][:25] if "OTHER_SIGNALS" in sections else []
    other_signals = [
        {"signal_id": row["signal_id"], "title": row["title"], "outlet": row["publisher"], "date": fmt_date(row["published_at"]), "url": row["url"], "priority": row["route"]["priority"], "tier": row.get("tier"), "rung": row.get("rung")}
        for row in others
    ]

    request_rows = []
    if "MANAGEMENT_REQUESTS" in sections:
        matched: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in shown:
            if row["route"].get("request_id"):
                matched[row["route"]["request_id"]].append(row)
        requests = [request for request in pool.management_requests()
                    if request.get("pack") in (None, pack_name) and request["status"] in {"ACTIVE", "PROPOSED"}]
        # A request this pack's signals did not answer may be answered by a child pack it covers, such as URS Taiwan.
        from_child = child_request_hits(pool, run_date, pack, [request for request in requests if not matched.get(request["request_id"])])
        for request in requests:
            hits = (matched.get(request["request_id"]) or from_child.get(request["request_id"]) or [])[:5]
            request_rows.append({
                "request_id": request["request_id"], "title": request["title"], "status": request["status"], "requested_by": request["requested_by"], "source": request.get("source"),
                "signals": [{"signal_id": row["signal_id"], "title": row["title"], "outlet": row["publisher"], "date": fmt_date(row["published_at"]), "url": row["url"],
                             **({"via": row["via"]} if row.get("via") else {})} for row in hits],
            })
        request_rows.sort(key=lambda row: (row["status"] != "ACTIVE", not row["signals"], row["request_id"]))
    request_ids = {item["signal_id"] for row in request_rows for item in row["signals"]}

    entity_packs = []
    if "ENTITY_PACKS" in sections:
        from grit.pipeline.reader_scan import ReaderRun, child_summaries

        entity_packs = child_summaries(pool, ReaderRun(run_id, run_date, pack_name), pack)

    watch_rows = []
    for row in watchpoints:
        update = updates.get(row["watchpoint_id"])
        watch_rows.append({
            "watchpoint_id": row["watchpoint_id"], "title": row["title"], "detail": row["detail"], "status": row["status"], "cycles": row["cycles_without_update"],
            "update": render(update["note"]) if update else None,
        })

    red = red_items(routed)
    confirmed = [item for item in red if item["status"] == "CONFIRMED"]
    pending = [item for item in red if item["status"] == "CANDIDATE"]

    def source_ref(signal_id: str) -> dict[str, Any]:
        signal = signals[signal_id]
        return {"signal_id": signal_id, "outlet": signal["publisher"], "date": fmt_date(signal["published_at"]), "url": signal["url"], "title": signal["title"]}

    qa_rows = []
    for row in qa:
        # A final Critic's objection to a report draft (REPORT:<profile>) concerns text a later draft replaced, often
        # another reader's. It stays in the run's QA log and weekly issues, not in this report.
        if str(row["item"]).startswith("REPORT:"):
            continue
        correction = render(row["correction"])
        if not correction["sources"]:
            correction["sources"] = [source_ref(signal_id) for signal_id in row.get("source_ids") or [] if signal_id in signals]
        qa_rows.append({"item": row["item"], "prior": row["prior"], "correction": correction, "origin": row["origin"]})

    # Format v2 (spec 44): Wilson's tables, by rule, from the same rows.
    from grit.pipeline.reader_scan import graph_pack
    from grit.report import format_v2

    v2 = format_v2.build(pool, run, pack, routed, condition_rows, {"Supplier": suppliers, "Customer": customers, "Competitor": competitors},
                         [pack.source_register, graph_pack(pack).source_register])
    v2_ids = {signal_id for table in ("escalating", "changes", "priorities") for row in v2[table] for signal_id in row.get("signal_ids") or []}
    v2_ids |= {signal_id for row in v2["escalating"] for signal_id in [*((row.get("row") or {}).get("source_ids") or []), *(row.get("support_ids") or [])]}
    v2_ids |= {signal_id for row in v2["look_ahead"] for signal_id in row["signal_ids"]}
    v2_ids |= {signal_id for line in v2["coverage"] for cell in line["cells"].values() for signal_id in cell["signal_ids"]}
    v2_ids |= {signal_id for row in v2["jurisdictions"] for signal_id in row["signal_ids"]}
    signals.update(_signals(pool, v2_ids - set(signals)))
    for row in v2["escalating"]:
        drafted = row.get("row")
        row["summary"] = render(drafted["summary"]) if drafted else None
        row["implications"] = render(drafted["implications"]) if drafted else None
        size = row.get("financial_size") or {}
        if row["implications"] and isinstance(size, Mapping) and size.get("label") and size["label"] != "not established":
            note = f" Financial size: {size['label']} ({size.get('currency') or 'SGD'} {size.get('amount_millions')} million)."
            sized = (row["implications"].get("plain") or "") + note
            row["implications"] = {**row["implications"], "plain": sized,
                                   "runs": [*(row["implications"].get("runs") or []), {"text": note}]}
        elif row["implications"] and isinstance(size, Mapping):
            note = " Financial size: not established."
            sized = (row["implications"].get("plain") or "") + note
            row["implications"] = {**row["implications"], "plain": sized,
                                   "runs": [*(row["implications"].get("runs") or []), {"text": note}]}
        row["response"] = (drafted or {}).get("response")
        cited = list((drafted or {}).get("source_ids") or row["signal_ids"][:3])
        cited += [signal_id for signal_id in row.get("support_ids") or [] if signal_id not in cited]
        row["sources"] = [source_ref(signal_id) for signal_id in cited if signal_id in signals]
    for table in ("changes", "priorities", "look_ahead", "jurisdictions"):
        for row in v2[table]:
            row["sources"] = [source_ref(signal_id) for signal_id in row.get("signal_ids") or [] if signal_id in signals]
    for line in v2["coverage"]:
        for cell in line["cells"].values():
            cell["sources"] = [source_ref(signal_id) for signal_id in cell["signal_ids"] if signal_id in signals]

    bottom = ((run.get("config") or {}).get("bottom_line") or {})
    escalating = [row for row in condition_rows if row["trend"] == "Escalating"]
    label = "RED" if confirmed else "ELEVATED" if pending or escalating or v2["escalating"] or any(row["status"] != "CLOSED" for row in watch_rows) else "STABLE"
    bottom_text = bottom.get("bottom_line") if bottom.get("status") == "VALIDATED" else fallback_bottom_line(condition_rows, checks, watch_rows, red, pack.label)
    escalation = bottom.get("escalation") if bottom.get("status") == "VALIDATED" else red_escalation(red)

    scenarios = None
    sections = list(brief_config.get("sections") or [])
    # Every pack with a reviewed scenario register shows its outlook: the Group pack and, from 30 October 2026, each business area.
    if True:
        from grit.pipeline.scenarios import outlook

        scenarios = outlook(pool, run_id, pack)
        if scenarios and "SCENARIOS" not in sections:
            sections.insert(sections.index("CONDITIONS") + 1 if "CONDITIONS" in sections else len(sections), "SCENARIOS")
    seen_ids = {signal_id for shift in (scenarios or {}).get("shifts") or [] for case in shift["cases"]
                for item in [*case["signposts_seen"], *case["counter_indicators_seen"]] for signal_id in item["signal_ids"]}
    membership = sorted({*ids, *v2_ids, *request_ids, *(row["signal_id"] for row in priority_rows), *(row["signal_id"] for row in others), *seen_ids})
    counts = pool.one("SELECT COUNT(*) AS signals, SUM(decision = 'KEEP') AS kept FROM assessments WHERE run_id = ?", (run_id,)) or {}
    queries = pool.one("SELECT COUNT(DISTINCT query_id) AS queries FROM sightings WHERE run_id = ?", (run_id,)) or {}
    agents = pool.rows("SELECT actor, version, model, status, COUNT(*) AS calls FROM execution_log WHERE run_id = ? AND actor_type = 'AGENT' GROUP BY actor, version, model, status ORDER BY actor", (run_id,))
    model = {
        "meta": {
            "title": brief_config.get("title") or f"GRIT-Scan: {pack_name}", "subtitle": brief_config.get("subtitle") or "Weekly scan",
            "prepared_for": brief_config.get("preparedFor") or profile.role, "reader": profile.label, "profile_id": profile.profile_id, "aperture": profile.aperture,
            "run_id": run_id, "run_date": run["run_date"], "window": window_label(run_date, window), "prior_run_date": fmt_date(previous["run_date"]) if previous else None,
            "grounded_in": _grounded_in(pack), "status": STATUS, "show_review_status": bool(brief_config.get("showReviewStatus", True)), "sections": sections,
            "pack": pack_name, "pack_label": pack.label, "pack_short": str((pack.manifest or {}).get("shortLabel") or pack.label), "level": pack.level, "section_titles": dict(plan_config.get("sectionTitles") or {}),
            "standing_label": "risk theme" if any(row.get("group") for row in condition_rows) else "condition",
        },
        "bottom_line": {"label": label, "text": bottom_text, "escalation": escalation, "drafted_by": "Reporter (validated)" if bottom.get("status") == "VALIDATED" else "rule (the Reporter's draft was withheld or absent)"},
        "order_book": pack.order_book,
        "projects": groups, "suppliers": suppliers, "customers": customers, "hazards": hazards, "entities": entities, "competitors": competitors,
        "management_requests": request_rows, "entity_packs": entity_packs,
        "conditions": condition_rows, "scenarios": scenarios, "findings": findings, "other_signals": other_signals, "qa": qa_rows, "watchpoints": watch_rows, "red": red, "red_triggers": red_trigger_labels(),
        "corrections": [ledger_view(row) for row in pool.corrections(pack_name)],
        "method": {
            "queries": queries.get("queries") or 0, "queries_label": "queries returned pages", "signals": counts.get("signals") or 0, "kept": counts.get("kept") or 0, "shown_to_reader": len(shown),
            "hidden_by_routing": sum(1 for row in routed if not row["route"]["include"]), "profile": profile.in_force,
            "moved_by_profile": sum(1 for row in shown if row["preference"]["affinity"]), "agents": agents,
        },
        "membership": membership,
        "v2": v2,
    }
    model["content_hash"] = canonical_hash({key: value for key, value in model.items() if key != "method"})
    return model


def _harm_line(row: Mapping[str, Any]) -> str:
    harm = row.get("harm") or {}
    if harm.get("status") != "SCORED":
        return f"Harm scorer not run: {harm.get('reason') or 'no input'}."
    severity = (harm.get("severity") or {}).get("max_band")
    transmission = (harm.get("transmission") or {}).get("rung")
    verdict = (harm.get("verdict") or {}).get("verdict")
    severity_text = "not established" if severity in {None, "NOT_ASSESSED"} else severity
    return f"Harm scorer: verdict {verdict}; transmission {transmission}; severity {severity_text} (no structured observation or value-at-risk yet)."


def _card_summary(card: Mapping[str, Any]) -> dict[str, Any]:
    q1 = card.get("what_happened") or {}
    return {
        "what_happened": q1.get("text") if isinstance(q1, Mapping) else q1,
        "hypothesis": (card.get("hypothesis") or {}).get("text") if isinstance(card.get("hypothesis"), Mapping) else card.get("hypothesis"),
        "confidence": (card.get("q6") or {}).get("level"),
        "confidence_reasons": (card.get("q6") or {}).get("reasons") or [],
        "status": card.get("status"),
    }


def _grounded_in(pack: Any) -> list[str]:
    if pack.level == "BA":
        names = [(pack.exposure or {}).get("workbook"), (pack.themes or {}).get("registerId"), (pack.management_focus or {}).get("registerId"), (pack.scan_plan or {}).get("registerId")]
        return [name for name in names if name]
    names = [pack.baseline.get("baselineId"), (pack.exposure or {}).get("registerVersion"), (pack.scan_plan or {}).get("registerId")]
    if pack.order_book:
        names.append(pack.order_book.get("source"))
    return [name for name in names if name]


def fallback_bottom_line(conditions: Sequence[Mapping[str, Any]], checks: Sequence[Mapping[str, Any]], watchpoints: Sequence[Mapping[str, Any]], red: Sequence[Any], label: str = "the reader pack") -> str:
    """A rule-written bottom line, used when the Reporter's draft is withheld."""
    found = [row["item_name"] for row in checks if row["status"] == "FOUND" and row["item_type"] not in {"HAZARD", "COMPETITOR"}]
    moved = [f"{row['name']} ({row['trend'].lower()})" for row in conditions if row["trend"] in {"Escalating", "Easing"}]
    parts = [
        f"Items this week name {', '.join(found[:4])}." if found else f"No item this week names a {label} entity, project, supplier or customer.",
        f"Standing items that moved: {'; '.join(moved)}." if moved else "No standing item moved on the evidence found.",
    ]
    open_items = [row["title"] for row in watchpoints if row["status"] != "CLOSED"]
    if open_items:
        parts.append(f"Watchpoints carried: {'; '.join(open_items)}.")
    parts += [line for line in [red_summary(red)] if line]
    return " ".join(parts)


def child_request_hits(pool: Pool, run_date: date, pack: Any, requests: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Kept signals of the pack's child runs for the same date that answer a request, by the same keyword rule as routing.
    Each carries the child pack's label in "via"."""
    from grit.core.routing import match_request
    from grit.pipeline.reader_scan import _load_pack, run_id_for

    found: dict[str, list[dict[str, Any]]] = defaultdict(list)
    active = [request for request in requests if request.get("status") == "ACTIVE"]
    for child in (getattr(pack, "children", None) or []) if active else []:
        child_run = run_id_for(run_date, child)
        if not pool.run(child_run):
            continue
        label = _load_pack(child).label
        for row in pool.weekly_assessments(child_run):
            hit = match_request({**row, "pack": pack.pack}, active, [pack.pack])
            if hit:
                found[hit[0]].append({**row, "via": f"{label} pack"})
    # A keyword alone is loose ("Typhoon" also hits a weather guide), so reports sharing more of the request's own words lead.
    words = {request["request_id"]: set(re.findall(r"[a-z]{5,}", str(request.get("title") or "").casefold())) for request in active}
    for request_id, rows in found.items():
        rows.sort(key=lambda row: (-len(words[request_id] & set(re.findall(r"[a-z]{5,}", str(row.get("title") or "").casefold()))),
                                   row.get("tier") or 9, str(row.get("signal_id"))))
    return found


def red_trigger_labels() -> dict[str, str]:
    """The RED triggers R&A confirms against (URS methodology, section 14), id -> label."""
    from grit.core.red_events import red_triggers

    return red_triggers()


def red_items(routed: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The report's RED list. A candidate the reviewed selection excluded is not listed. The status basis still counts it
    (spec 45)."""
    return [{"signal_id": row["signal_id"], "title": row["title"], "url": row["url"], "rule": row.get("red_rule") or {},
             "status": row["red_status"], "decision": row.get("red_decision")} for row in routed
            if row.get("red") and (row.get("editorial") or {}).get("decision") != "EXCLUDE"]


def red_summary(red: Sequence[Mapping[str, Any]]) -> str:
    """RED status follows the R&A decision, never the rule alone."""
    confirmed = sum(1 for item in red if item.get("status") == "CONFIRMED")
    pending = sum(1 for item in red if item.get("status", "CANDIDATE") == "CANDIDATE")
    parts = []
    if confirmed:
        parts.append(f"R&A confirmed the RED impact of {confirmed} item(s).")
    if pending:
        parts.append(f"{pending} RED candidate(s) await R&A impact confirmation.")
    return " ".join(parts)


def red_escalation(red: Sequence[Mapping[str, Any]]) -> str:
    return red_summary(red) or "Nothing new this week independently warrants escalation before the next weekly cycle."
