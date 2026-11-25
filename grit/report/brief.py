"""The brief model: one reader's weekly brief as plain data, built from the pool. Renderers turn it into Word, PowerPoint,
Excel, or Markdown. Every format says the same thing.

The model applies the reader's routing (spec 24 aperture and spec 29 priority) and reader profile (spec 39) to the shared
signals. The profile orders signals within a priority tier and hides none. It never changes a shared fact. Citations stay as signal ids until a renderer prints the outlet, the date, and the link.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

from grit.core.canonical import canonical_hash
from grit.core.corrections import ledger_view
from grit.core.routing import aperture_routing
from grit.ontology.pack import load_ontology_pack
from grit.pipeline.exposure_snapshot import EXPOSURE_MAX_AGE_DAYS, ExposureRecord, PrincipalAsk
from grit.pool import Pool
from grit.profiles import Profile
from grit.profiles.emphasis import profile_effect
from grit.profiles.memory import build_memory
from grit.settings import CONFIG_DIR

CITE = re.compile(r"\[((?:SIG-[0-9a-f]{16})(?:\s*,\s*SIG-[0-9a-f]{16})*)\]")
STATUS = "DRAFT_FOR_REVIEW"
PROJECT_TIER_LABEL = {"TIER_1_ACTIVE": "Tier-1 active", "PIPELINE": "Pipeline"}


@dataclass(frozen=True)
class TierOnePhysicalThreat:
    signal_id: str
    title: str
    tier: int
    reader_band: str
    override_rule_id: str
    override_reason: str
    outlet: str
    published_at: str
    url: str | None


@dataclass(frozen=True)
class ExposureAskBlock:
    """Typed Exposure & Ask report block built from the approved contracts."""

    pack: str
    exposures: tuple[ExposureRecord, ...]
    principal_ask: PrincipalAsk | None
    owner_label: str | None
    owner_role: str | None
    red_triggers: tuple[str, ...]
    tier1_physical_threats: tuple[TierOnePhysicalThreat, ...]
    stale_warning: str | None


def _exposure_ask_raw(config: Mapping[str, Any]) -> Mapping[str, Any] | None:
    direct = config.get("principal_ask") or config.get("principalAsk")
    context = config.get("weekly_context") or {}
    snapshot = config.get("exposure_snapshot") or {}
    value = (
        direct
        or context.get("principal_ask")
        or context.get("principalAsk")
        or snapshot.get("principal_ask")
        or snapshot.get("principalAsk")
    )
    return value if isinstance(value, Mapping) else None


def exposure_ask_block(
    run: Mapping[str, Any],
    routed: Sequence[Mapping[str, Any]],
    *,
    profiles: Any = None,
) -> ExposureAskBlock:
    """Normalise the approved exposure snapshot and principal ask for one pack.

    Physical-threat overrides are collected from every routed assessment before the
    Spec-49 section cap is applied.
    """
    from grit.core.overrides import OVERRIDE_STAFF_THREAT, reader_band_of
    from grit.pipeline.exposure_snapshot import (
        exposure_freshness,
        normalize_exposure_records,
        normalize_principal_ask,
    )
    from grit.profiles import load_profiles

    config = run.get("config") or {}
    pack = str(run.get("pack") or "").strip()
    snapshot = config.get("exposure_snapshot")
    exposures: tuple[Any, ...] = ()
    stale_warning = None
    if isinstance(snapshot, Mapping) and not snapshot.get("notSupplied"):
        # snapshot_summary intentionally omits row references from the report
        # view. Restore its source-level reference before the typed normaliser;
        # no snapshot field is interpreted here.
        report_snapshot = dict(snapshot)
        report_snapshot.setdefault("pack", pack)
        source_reference = "; ".join(str(item) for item in snapshot.get("sources") or [])
        for section in ("sites", "travel", "projects", "financial", "alerts"):
            if section in report_snapshot:
                report_snapshot[section] = [
                    {**dict(row), "reference": str(row.get("reference") or source_reference)}
                    for row in report_snapshot.get(section) or []
                ]
        exposures = tuple(normalize_exposure_records(report_snapshot, pack=pack))
        run_date = date.fromisoformat(str(run["run_date"]))
        as_at_text, stale = exposure_freshness(snapshot, run_date=run_date)
        as_at = date.fromisoformat(as_at_text)
        if stale:
            stale_warning = (
                f"STALE EXPOSURE DATA: the approved snapshot is dated "
                f"{as_at.isoformat()}. It is older than {EXPOSURE_MAX_AGE_DAYS} days "
                "or does not agree with the report run date. Reconfirm staff counts and safety."
            )

    known_profiles = load_profiles() if profiles is None else profiles
    profile_map = (
        known_profiles if isinstance(known_profiles, Mapping)
        else {str(profile.profile_id): profile for profile in known_profiles}
    )
    ask_raw = _exposure_ask_raw(config)
    ask = normalize_principal_ask(ask_raw, pack=pack, profiles=profile_map) if ask_raw is not None else None
    profile = profile_map.get(ask.owner_profile_id) if ask else None
    threats = []
    seen: set[str] = set()
    for row in routed:
        priority = row.get("priority") if isinstance(row.get("priority"), Mapping) else row.get("priority_json")
        if isinstance(priority, str):
            continue
        if not isinstance(priority, Mapping) or priority.get("override_rule_id") != OVERRIDE_STAFF_THREAT:
            continue
        editorial = row.get("editorial") if isinstance(row.get("editorial"), Mapping) else {}
        editorial_decision = editorial.get("decision")
        if editorial_decision is not None:
            if editorial_decision not in {"INCLUDE", "WATCH"}:
                continue
        elif row.get("decision") not in {"KEEP", "INCLUDE", "WATCH"}:
            continue
        signal_id = str(row.get("signal_id") or "")
        if signal_id in seen:
            continue
        seen.add(signal_id)
        threats.append(TierOnePhysicalThreat(
            signal_id=signal_id,
            title=str(row.get("title") or signal_id),
            tier=1,
            reader_band=reader_band_of(row),
            override_rule_id=OVERRIDE_STAFF_THREAT,
            override_reason=str(priority.get("override_reason") or ""),
            outlet=str(row.get("publisher") or "source"),
            published_at=str(row.get("published_at") or "")[:10],
            url=str(row["url"]) if row.get("url") is not None else None,
        ))
    return ExposureAskBlock(
        pack=pack,
        exposures=exposures,
        principal_ask=ask,
        owner_label=getattr(profile, "label", None),
        owner_role=getattr(profile, "role", None),
        red_triggers=tuple(ask.red_triggers) if ask else (),
        tier1_physical_threats=tuple(threats),
        stale_warning=stale_warning,
    )


def exposure_ask_record(block: ExposureAskBlock) -> dict[str, Any]:
    """The serialisable report-model view of a typed ExposureAskBlock."""
    ask = block.principal_ask
    return {
        "pack": block.pack,
        "exposures": [
            {
                "country": row.country,
                "entity": row.entity,
                "staff_count": row.staff_count,
                "staff_scope": row.staff_scope,
                "snapshot_date": row.snapshot_date.isoformat(),
                "last_safety_confirmation": (
                    row.last_safety_confirmation.isoformat() if row.last_safety_confirmation else None
                ),
                "status": row.status,
                "record_type": row.record_type,
            }
            for row in block.exposures
        ],
        "principal_ask": None if ask is None else {
            "text": ask.text,
            "owner_profile_id": ask.owner_profile_id,
            "owner_label": block.owner_label,
            "owner_role": block.owner_role,
            "due_date": ask.due_date.isoformat(),
        },
        "red_triggers": list(block.red_triggers),
        "tier1_physical_threats": [
            {
                "signal_id": row.signal_id,
                "title": row.title,
                "tier": row.tier,
                "reader_band": row.reader_band,
                "override_rule_id": row.override_rule_id,
                "override_reason": row.override_reason,
                "outlet": row.outlet,
                "published_at": row.published_at,
                "url": row.url,
            }
            for row in block.tier1_physical_threats
        ],
        "stale_warning": block.stale_warning,
    }


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
    from grit.agents.checks import INTERNAL_CLAIM

    for match in CITE.finditer(text or ""):
        if match.start() > last:
            runs.append({"text": text[last : match.start()]})
        sentence = re.split(r"(?<=[.!?])\s+", text[last : match.start()].strip())[-1]
        if INTERNAL_CLAIM.search(sentence):
            last = match.end()  # no outlet can stand behind the business's own status, so the mark is dropped
            continue
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
    off_scope = out_of_scope(str(run.get("pack") or ""))
    out = []
    for row in rows:
        if row.get("red"):
            decision = decisions.get(row["signal_id"])
            row = {**row, "red_status": decision["decision"] if decision else "CANDIDATE", "red_decision": decision}
        route = aperture_routing(row, aperture=profile.aperture, packs=profile.packs, requests=requests)
        effect = profile_effect(row, profile, protected=bool(row.get("red")) or route.priority == "P1", memory=memory)
        selected = (row.get("editorial") or {}).get("decision", "INCLUDE") in {"INCLUDE", "WATCH"}
        # A headline the pack names as outside its business, such as a defence programme in a commercial pack, is not shown.
        # A RED candidate is always shown.
        if selected and off_scope and not row.get("red") and off_scope.search(str(row.get("title") or "")):
            selected = False
        out.append({**row, "route": route.as_record(), "preference": effect.as_record(), "shown": route.include and selected})
    from grit.agents.weekly import order_in_band_of
    from grit.core.overrides import band_sort_key, reader_band_of

    out.sort(key=lambda row: str(row.get("published_at") or ""), reverse=True)
    out.sort(key=lambda row: (
        not row["shown"],
        row["route"]["priority"] or "P9",
        band_sort_key(reader_band_of(row)),
        order_in_band_of(row),
        -row["preference"]["affinity"],
        row.get("tier") or 9,
        not row.get("condition_ids"),
        -(row.get("corroboration") or 1),
    ))
    return out


def out_of_scope(pack_name: str) -> re.Pattern[str] | None:
    """The pack.json "outOfScope" headline terms as one pattern, or None when the pack names none."""
    from grit.ontology.pack import manifests

    terms = [str(term).strip() for term in (manifests().get(pack_name) or {}).get("outOfScope") or [] if str(term).strip()]
    if not terms:
        return None
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(term) for term in terms) + r")(?![A-Za-z0-9])", re.IGNORECASE)


def build_brief(pool: Pool, run_id: str, profile: Profile) -> dict[str, Any]:
    run = pool.run(run_id)
    if run is None:
        raise KeyError(f"No run {run_id} in the pool.")
    pack_name = run["pack"]
    brief_config = profile.brief_for(pack_name)
    if brief_config is None:
        raise ValueError(f"{profile.label} receives no brief for the {pack_name} pack.")
    pack = load_ontology_pack(CONFIG_DIR, pack_name)
    if pack.level in {"BA", "ENTITY"}:
        return business_area_brief(pool, run, pack, profile, brief_config)
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

    from grit.core.overrides import BAND_ELEVATED, BAND_ESCALATING, reader_band_of

    shown = [row for row in routed if row["shown"]]
    max_findings = int(brief_config.get("maxFindings") or 8)
    # The package holds what the reader receives, so a section the profile leaves out adds no member.
    # Prefer reader bands Escalating/Elevated; fall back to legacy tier ≤ 2 when band is absent.
    priority_rows = [
        row for row in shown
        if reader_band_of(row) in {BAND_ESCALATING, BAND_ELEVATED} or (row.get("tier") or 9) <= 2
    ][:max_findings] if "PRIORITY_FINDINGS" in sections else []
    findings = []
    from grit.agents.weekly import reach_fields_of

    for row in priority_rows:
        card = (cards.get(row["signal_id"]) or {}).get("card")
        reach = reach_fields_of(row)
        findings.append({
            "signal_id": row["signal_id"], "title": clean_title(row["title"]) or row["title"], "outlet": row["publisher"], "date": fmt_date(row["published_at"]), "url": row["url"],
            "priority": row["route"]["priority"], "band": reader_band_of(row), "tier": row.get("tier"), "rung": row.get("rung"),
            "order_in_band": reach.get("order_in_band"), "reach_channel": reach.get("reach_channel"),
            "urs_reach_reason": reach.get("urs_reach_reason"),
            "why": row["route"]["matched"][:2] or [row.get("relevance_basis")],
            "risks": [f"{hit['level'].title()}: {hit['name']}" for hit in (row.get("risk_cascade") or [])[:3]],
            "corroboration": row.get("corroboration"), "red": row.get("red_status") == "CONFIRMED", "red_status": row.get("red_status"), "harm": _harm_line(row), "preference": row["preference"]["reasons"],
            "card": _card_summary(card) if card else None,
        })
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
        qa_rows.append({"item": row["item"], "prior": row["prior"], "correction": correction, "origin": row["origin"],
                        "source_ids": list(row.get("source_ids") or [])})

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
        if scenarios:
            by_id = {row["condition_id"]: row for row in condition_rows}
            for item in scenarios.get("indicators") or []:
                item["themes"] = [{"id": theme, "number": by_id[theme]["number"], "name": by_id[theme]["name"], "trend": by_id[theme]["trend"]}
                                  for theme in item.get("themes") or [] if theme in by_id]
        if scenarios and "SCENARIOS" not in sections:
            sections.insert(sections.index("CONDITIONS") + 1 if "CONDITIONS" in sections else len(sections), "SCENARIOS")
    seen_ids = {signal_id for shift in (scenarios or {}).get("shifts") or [] for case in shift["cases"]
                for item in [*case["signposts_seen"], *case["counter_indicators_seen"]] for signal_id in item["signal_ids"]}
    membership = sorted({*ids, *v2_ids, *(row["signal_id"] for row in priority_rows), *seen_ids})
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
        "conditions": condition_rows, "scenarios": scenarios, "findings": findings, "qa": qa_rows, "watchpoints": watch_rows, "red": red, "red_triggers": red_trigger_labels(),
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


SECTION_TITLES = {
    "MANAGEMENT_FOCUS": "Management Focus Topics", "REGISTER_RISK": "Key, T2 & Emerging", "OTHER_THEME": "Other Risk Themes",
    "TREND": "Trends/Developments",
}


def meta_for(run: Mapping[str, Any], pack: Any, profile: Profile, brief_config: Mapping[str, Any]) -> dict[str, Any]:
    from grit.pipeline.reader_scan import scan_window

    run_date = date.fromisoformat(run["run_date"])
    return {
        "title": brief_config.get("title") or f"GRIT-Scan: {pack.pack}", "subtitle": brief_config.get("subtitle") or "Weekly scan",
        "prepared_for": brief_config.get("preparedFor") or profile.role, "reader": profile.label, "profile_id": profile.profile_id, "aperture": profile.aperture,
        "run_id": run["run_id"], "run_date": run["run_date"], "window": window_label(run_date, scan_window(pack)),
        "grounded_in": _grounded_in(pack), "status": STATUS, "show_review_status": bool(brief_config.get("showReviewStatus", True)),
        "pack": pack.pack, "pack_label": pack.label, "pack_short": str((pack.manifest or {}).get("shortLabel") or pack.label), "level": pack.level,
    }


def business_area_brief(pool: Pool, run: Mapping[str, Any], pack: Any, profile: Profile, brief_config: Mapping[str, Any]) -> dict[str, Any]:
    """A business area's weekly brief (Wilson's layout of 19 November 2026): the summary, four event tables (management
    focus topics; key, tier-2 and emerging risks; other risk themes; trends and developments), then Appendix A (look-ahead,
    scenarios, jurisdictions, named watch) and Appendix B (the source register). The EventAssessor drafted each row and
    chose nothing: the section of an event follows the rule in grit.pipeline.events.section_of."""
    from grit.agents.reader import format_timeline
    from grit.pipeline.events import (
        MANAGEMENT_FOCUS,
        MAX_SECTION_ROWS,
        OTHER_THEME,
        SECTIONS,
        event_register,
        label_merges,
        risk_names,
        same_relation_pairs,
    )
    from grit.pipeline.reader_scan import graph_pack
    from grit.pipeline.scenarios import outlook
    from grit.report.format_v2 import jurisdiction_rows, named_watch, source_tiers
    from grit.report.lookahead import look_ahead

    run_id, run_date = run["run_id"], date.fromisoformat(run["run_date"])
    config = run.get("config") or {}
    routed = routed_signals(pool, run_id, profile)
    shown = [row for row in routed if row["shown"]]
    names = risk_names(pack)
    drafted = config.get("event_rows") or {}
    merges = dict(config.get("event_merges") or {})
    same_pairs = same_relation_pairs(pool, run_id)
    events = event_register(pool, run_id, rows=shown, names=names, merges=merges)
    # Drafted rows of one story that the recorded merges missed, for example after a merge rule changed, join here too.
    extra = {key: value for key, value in label_merges([event for event in events if event["members"]], drafted, same_pairs=same_pairs).items() if key not in merges}
    if extra:
        events = event_register(pool, run_id, rows=shown, names=names, merges={**merges, **extra})
    withheld = set(config.get("event_rows_withheld") or [])
    # The QA log withholds these events, so their reports feed no table and no look-ahead.
    hidden = {signal_id for event in events if event["event_id"] in withheld for signal_id in event["signal_ids"]}
    visible = [row for row in shown if row["signal_id"] not in hidden]
    titles = {row["request_id"]: row["title"] for row in pool.management_requests()}

    checks = pool.exposure_checks(run_id)
    qa = pool.qa_log(run_id)
    ids = {signal_id for row in drafted.values() for signal_id in row.get("source_ids") or []}
    ids |= _cited_ids(*(row.get("summary") for row in checks)) | {signal_id for row in checks for signal_id in row.get("signal_ids") or []}
    ids |= {signal_id for row in qa for signal_id in row.get("source_ids") or []}
    signals = _signals(pool, ids)

    def render(text: str | None) -> dict[str, Any] | None:
        if not text:
            return None
        runs, cited = citations(text, signals)
        return {"runs": runs, "plain": plain(runs), "sources": cited}

    def source_ref(signal_id: str) -> dict[str, Any]:
        signal = signals[signal_id]
        return {"signal_id": signal_id, "outlet": signal["publisher"], "date": fmt_date(signal["published_at"]), "url": signal["url"], "title": signal["title"]}

    sections: dict[str, list[dict[str, Any]]] = {key: [] for key in SECTIONS}
    for event in events:
        row = drafted.get(event["event_id"])
        section = (row or {}).get("section")
        if not row or section not in sections or event["event_id"] in withheld or len(sections[section]) >= MAX_SECTION_ROWS:
            continue
        if section == MANAGEMENT_FOCUS:
            risk = "; ".join(f"MF: {titles.get(item, item)}" for item in event["requests"][:2] if item in titles)
        elif section == OTHER_THEME:
            risk = "; ".join(f"Group: {name}" for name in event["group_risks"][:2])
        else:
            risk = event["risk_label"] or "Trend"
        summary = row["summary"] + (" " + format_timeline(row["sequence"], "") if row.get("sequence") else "")
        sections[section].append({
            "event_id": event["event_id"], "band": event["band"], "item": row["item"], "risk": risk, "summary": render(summary),
            "impact": render(row.get("implications")), "opportunity": render(row.get("opportunity")), "response": row.get("response"),
            "signal_ids": list(row.get("source_ids") or []),
            "sources": [source_ref(signal_id) for signal_id in row.get("source_ids") or [] if signal_id in signals],
        })

    # Appendix A: look-ahead from sources linked to the business area only, scenarios, jurisdictions and the named watch.
    event_of = {signal_id: event["event_id"] for event in events for signal_id in event["signal_ids"]}
    linked = [row for row in visible if row.get("rung") or row.get("condition_ids") or (row.get("route") or {}).get("request_id")]
    look = look_ahead(linked, run_date, event_of=event_of,
                      relevance=lambda row: risk_labels_for(row, names))
    jurisdictions = jurisdiction_rows(pack, visible, events, pool.answered_searches(run_id))
    named: dict[str, list[dict[str, Any]]] = {"Supplier": [], "Customer": [], "Competitor": []}
    kinds = {"SUPPLIER": "Supplier", "CUSTOMER": "Customer", "COMPETITOR": "Competitor"}
    for row in checks:
        if row["item_type"] in kinds:
            named[kinds[row["item_type"]]].append({"item_id": row["item_id"], "name": row["item_name"], "status": row["status"],
                                                    "text": render(row["summary"]), "lesson": row.get("lesson")})
    watch, _passing = named_watch(named)
    scenarios = outlook(pool, run_id, pack)
    if scenarios:
        scenarios.pop("risk_links", None)  # the Group radar's register cascade; a business-area brief does not show it
    more = {signal_id for row in look for signal_id in row["signal_ids"]} | {signal_id for row in jurisdictions for signal_id in row["signal_ids"]}
    signals.update(_signals(pool, more - set(signals)))
    look_rows = [{**item, "date": item["date"].isoformat(),
                  "sources": [source_ref(signal_id) for signal_id in item["signal_ids"] if signal_id in signals]} for item in look]
    for row in jurisdictions:
        row["sources"] = [source_ref(signal_id) for signal_id in row["signal_ids"] if signal_id in signals]

    red = red_items(routed)
    rows = [row for table in sections.values() for row in table]
    status = "RED" if any(item["status"] == "CONFIRMED" for item in red) else "ELEVATED" if any(row["band"] == "\u25b2" for row in rows) else "STABLE"
    bottom = config.get("bottom_line") or {}
    validated = bottom.get("status") == "VALIDATED"
    seen_ids = {signal_id for shift in (scenarios or {}).get("shifts") or [] for case in shift["cases"]
                for item in [*case["signposts_seen"], *case["counter_indicators_seen"]] for signal_id in item["signal_ids"]}
    watch_ids = {ref["signal_id"] for item in watch for ref in (item["text"] or {}).get("sources") or []}
    exposure_ask = exposure_ask_record(exposure_ask_block(run, routed))
    threat_ids = {str(item["signal_id"]) for item in exposure_ask["tier1_physical_threats"] if item.get("signal_id")}
    membership = sorted({
        *(signal_id for row in rows for signal_id in row["signal_ids"]),
        *more, *watch_ids, *seen_ids, *threat_ids,
    })
    counts = pool.one("SELECT COUNT(*) AS signals, SUM(decision = 'KEEP') AS kept FROM assessments WHERE run_id = ?", (run_id,)) or {}
    model = {
        "meta": {**meta_for(run, pack, profile, brief_config), "layout": "BA", "section_titles": dict(SECTION_TITLES)},
        "bottom_line": {"label": status, "text": bottom.get("bottom_line") if validated else ba_fallback_summary(sections, watch, red, pack.label),
                        "drafted_by": "Reporter (validated)" if validated else "rule (the Reporter's draft was withheld or absent)"},
        "v2": {"status": status, "sections": sections, "look_ahead": look_rows, "jurisdictions": jurisdictions, "watch": watch,
               "tiers": source_tiers(routed, [pack.source_register, graph_pack(pack).source_register]),
               "short": str((pack.manifest or {}).get("shortLabel") or pack.label),
               "exposure_ask": exposure_ask},
        "scenarios": scenarios, "red": red,
        # Not shown to the reader: the QA log lets the release check find a withheld date that reappears.
        "qa": [{"item": row["item"], "prior": row["prior"], "correction": row["correction"], "origin": row["origin"],
                "source_ids": list(row.get("source_ids") or [])} for row in qa if not str(row["item"]).startswith("REPORT:")],
        "method": {"signals": counts.get("signals") or 0, "kept": counts.get("kept") or 0, "shown_to_reader": len(shown), "profile": profile.in_force,
                   "hidden_by_routing": sum(1 for row in routed if not row["route"]["include"]),
                   "moved_by_profile": sum(1 for row in shown if row["preference"]["affinity"])},
        "membership": membership,
    }
    model["content_hash"] = canonical_hash({key: value for key, value in model.items() if key != "method"})
    return model


def risk_labels_for(row: Mapping[str, Any], names: Mapping[str, str]) -> str:
    from grit.pipeline.events import risk_labels

    return risk_labels([str(item) for item in row.get("condition_ids") or []], names) or str(row.get("rung") or "").title()


def ba_fallback_summary(sections: Mapping[str, Sequence[Mapping[str, Any]]], watch: Sequence[Mapping[str, Any]], red: Sequence[Any], label: str) -> str:
    """A rule-written summary, used when the Reporter's draft is withheld."""
    counts = [f"{len(rows)} {SECTION_TITLES[key].lower()}" for key, rows in sections.items() if rows]
    parts = [f"This week's {label} items: {', '.join(counts)}." if counts else f"No {label} item passed its checks this week."]
    rising = [row["item"] for rows in sections.values() for row in rows if row["band"] == "\u25b2"]
    if rising:
        parts.append(f"New or rising: {'; '.join(rising[:3])}.")
    if watch:
        parts.append(f"Named watch: {', '.join(item['name'] for item in watch[:4])}.")
    parts += [line for line in [red_summary(red)] if line]
    return " ".join(parts)


def clean_title(title: Any) -> str:
    from grit.report.format_v2 import clean_title as clean

    return clean(title)


def _harm_line(row: Mapping[str, Any]) -> str:
    """The harm verdict for the reader. A scorer that did not run says nothing: its reason is pipeline state, logged elsewhere."""
    harm = row.get("harm") or {}
    if harm.get("status") != "SCORED":
        return ""
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
