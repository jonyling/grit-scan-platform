"""Shape pool records for the screens. Pure. Absence reads as "not established", never as a blank or a zero."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

NOT_ESTABLISHED = "not established"
_ABSENCE = {"NOT_ASSESSED", "INSUFFICIENT_DATA", "NOT_ESTABLISHED"}


def shown(value: Any) -> str:
    if value is None or value == "" or value == []:
        return NOT_ESTABLISHED
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(item) for item in value)
    text = str(getattr(value, "value", value))
    return NOT_ESTABLISHED if text in _ABSENCE else text


def kpis(routed: Sequence[Mapping[str, Any]], brief: Mapping[str, Any], packages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    shown_rows = [row for row in routed if row["shown"]]
    return {
        "Pages searched": brief["method"]["signals"],
        "Kept by rule": brief["method"]["kept"],
        "In your view": len(shown_rows),
        "Tier 1–2": sum(1 for row in shown_rows if (row.get("tier") or 9) <= 2),
        "RED drafts": sum(1 for row in routed if row.get("red")),
        "Pending review": sum(1 for row in packages if row["status"] == "PENDING_REVIEW"),
    }


def profile_in_force(profile: Any) -> list[str]:
    """The sidebar lines: which reader profile shapes this view and what it moves up, down, or trusts."""
    lines = [profile.in_force]
    reader = profile.reader
    if reader is None:
        return lines
    names = reader.names
    for key, label in (("emphasis", "Moves up"), ("de_emphasis", "Moves down")):
        section = reader.section(key)
        items = [names.get(item, item) for item in section.get("conditions") or []] + list(section.get("topics") or []) + list(section.get("countries") or [])
        if items:
            lines.append(f"{label}: " + ", ".join(items[:12]) + (f", and {len(items) - 12} more" if len(items) > 12 else "") + ".")
    sources = reader.section("sources")
    if sources.get("trust"):
        lines.append(f"Trusted sources: {len(sources['trust'])}.")
    if sources.get("distrust"):
        lines.append("Distrusted: " + ", ".join(sources["distrust"]) + ".")
    lines.append("The profile reorders within a priority tier. It hides nothing and never moves a P1 or RED signal down.")
    return lines


def signal_rows(routed: Sequence[Mapping[str, Any]], decisions: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """decisions is None unless the reader's profile turns on the signal buttons; then a "Your decision" column appears."""
    return [
        {
            "Priority": row["route"]["priority"] or "", "Tier": row.get("tier"), "RED": "RED" if row.get("red") else "", "Title": row["title"],
            "Outlet": row.get("publisher"), "Published": (row.get("published_at") or "")[:10], "Rung": row.get("rung") or "",
            "Conditions": ", ".join(row.get("condition_ids") or []), "Named items": ", ".join(hit["item_name"] for hit in row.get("exposure") or [] if hit.get("strong")),
            **({"Your decision": decisions.get(row["signal_id"], "")} if decisions is not None else {}),
            "Your profile": " ".join(row["preference"]["reasons"])[:120], "Shown": "yes" if row["shown"] else "no", "signal_id": row["signal_id"],
        }
        for row in routed
    ]


def signal_detail(row: Mapping[str, Any]) -> list[tuple[str, str]]:
    harm = row.get("harm") or {}
    severity = (harm.get("severity") or {}).get("max_band")
    lines = [
        ("Why it is here", " ".join(row["route"]["matched"]) or shown(row.get("relevance_basis"))),
        ("Link", f"{shown(row.get('rung'))}. " + " ".join((row.get("link") or {}).get("reasons") or [])),
        ("Named items", shown([f"{hit['item_type'].title()} {hit['item_name']} ({'strong' if hit.get('strong') else 'passing'})" for hit in row.get("exposure") or []])),
        ("Standing conditions", shown(row.get("condition_ids"))),
        ("Risk register", shown([f"{hit['level'].title()} {hit['risk_id']} {hit['name']} ({hit['basis'].lower()})" for hit in (row.get("risk_cascade") or [])[:6]])),
        ("Briefing priority", f"Tier {row.get('tier')}. " + " ".join((row.get("priority") or {}).get("reasons") or [])),
        ("Outlets carrying it", f"{row.get('corroboration')}: " + ", ".join(item.get("publisher") or "" for item in (row.get("link") or {}).get("also_reported_by") or [])),
        ("Evidence confidence", shown(row.get("evidence_confidence"))),
        ("Harm scorer", f"verdict {shown((harm.get('verdict') or {}).get('verdict'))}; transmission {shown((harm.get('transmission') or {}).get('rung'))}; severity {shown(severity)}; opportunity cost {shown(row.get('opportunity_cost'))}"),
        ("RED rule", " ".join((row.get("red_rule") or {}).get("reasons") or []) or "No RED pattern matched."),
        ("Your profile", " ".join(row["preference"]["reasons"]) or "Your profile does not move this signal."),
    ]
    return [(label, value) for label, value in lines if value]
