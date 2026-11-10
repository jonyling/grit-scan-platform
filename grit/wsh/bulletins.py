"""WSH Council Alert / Advisory / Insights helpers (spec 47 bulletin depth)."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

SERIES = ("ALERT", "ADVISORY", "INSIGHTS")
SERIES_LABEL = {"ALERT": "WSH Alert", "ADVISORY": "WSH Advisory", "INSIGHTS": "WSH Insights"}

_AUTHORITY_DOMAINS = ("wshc.sg", "mom.gov.sg", "mom.gov", "straitstimes.com", "channelnewsasia.com")
_COUNCIL_OR_MOM = ("wshc.sg", "mom.gov.sg", "workplace safety and health council", "ministry of manpower", "wsh council")

_CLUSTER_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("heat-stress", (r"heat\s*stress", r"\bwbgt\b", r"heat.?mitigat", r"heat.?strain")),
    ("haze-pm25", (r"\bhaze\b", r"pm\s*2\.?\s*5", r"\bpsi\b")),
    ("safety-timeout", (r"safety\s+time-?out", r"after\s+\d+\s+(?:deaths|fatalities)", r"stop-?work\s+(?:order|campaign)")),
    ("sif-alliance", (r"sif\s+prevention\s+alliance", r"osha.{0,40}csra", r"csra.{0,40}osha", r"national\s+serious\s+injury\s+and\s+fatality")),
)

_ENFORCEMENT = (
    r"safety\s+time-?out",
    r"after\s+\d+\s+(?:deaths|fatalities)",
    r"tightens?\s+enforcement",
    r"heightened\s+(?:penalties|enforcement)",
    r"stop-?work\s+(?:order|campaign)",
)


def _plain(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _blob(title: str = "", body: str = "", url: str = "", publisher: str = "") -> str:
    return f"{title}\n{body}\n{url}\n{publisher}".casefold()


def detect_series(title: str = "", body: str = "", url: str = "", publisher: str = "") -> str | None:
    """Return ALERT, ADVISORY or INSIGHTS when the page is a WSH Council series item."""
    text = _blob(title, body, url, publisher)
    if "wsh alert" in text:
        return "ALERT"
    if "wsh advisory" in text:
        return "ADVISORY"
    if "wsh insights" in text:
        return "INSIGHTS"
    return None


def is_bulletin(title: str = "", body: str = "", url: str = "", publisher: str = "") -> bool:
    return detect_series(title, body, url, publisher) is not None


def is_authority_source(url: str = "", publisher: str = "") -> bool:
    blob = f"{url} {publisher}".casefold()
    return any(domain in blob for domain in _AUTHORITY_DOMAINS) or any(cue in blob for cue in _COUNCIL_OR_MOM)


def is_council_or_mom(url: str = "", publisher: str = "") -> bool:
    blob = f"{url} {publisher}".casefold()
    return any(cue in blob for cue in _COUNCIL_OR_MOM)


def is_scanned_pdf_only(title: str = "", body: str = "", url: str = "") -> bool:
    """True when the hit looks like an image-only PDF with no readable body."""
    text = _plain(body)
    if len(text) >= 80:
        return False
    return bool(re.search(r"\.pdf(\b|$)", (url or "").casefold())) or "pdf" in (title or "").casefold()


def is_enforcement_campaign(title: str = "", body: str = "") -> bool:
    text = f"{title}\n{body}".casefold()
    return any(re.search(pattern, text) for pattern in _ENFORCEMENT)


def cluster_key(title: str = "", body: str = "", *, series: str | None = None) -> str | None:
    """Normalized subject for collapsing one development into one note."""
    text = f"{title}\n{body}".casefold()
    for key, patterns in _CLUSTER_RULES:
        if any(re.search(pattern, text) for pattern in patterns):
            return key
    if series:
        # Same Council title stem collapses alert rewrites of one bulletin.
        stem = re.split(r"\s+[|\-–—]\s+", _plain(title).casefold(), maxsplit=1)[0].strip()
        stem = re.sub(r"\s+", " ", stem)
        if stem:
            return f"bulletin:{stem}"
    return None


def authority_rank(url: str = "", publisher: str = "", decision: str = "WATCH") -> tuple[int, int]:
    """Lower is better: Council/MOM first, then INCLUDE, then other authority domains."""
    if is_council_or_mom(url, publisher):
        primary = 0
    elif is_authority_source(url, publisher):
        primary = 1
    else:
        primary = 2
    decision_rank = 0 if decision == "INCLUDE" else 1 if decision == "WATCH" else 2
    return primary, decision_rank


def fill_energy(classification: Mapping[str, Any], current: str | None = None) -> str:
    """Write the first matched high-energy hazard when the model left energy blank."""
    if current and current.strip() and current.strip().casefold() != "not established":
        return current
    hazards = classification.get("hazards") or []
    if hazards:
        return str(hazards[0].get("label") or hazards[0].get("id") or "not established")
    return "not established"


def route_section(section_id: str | None, title: str = "", body: str = "", *, series: str | None = None) -> str | None:
    """Move enforcement campaigns to SERIOUS. Alerts default to SERIOUS when section is missing."""
    if is_enforcement_campaign(title, body):
        return "SERIOUS"
    if series == "ALERT" and section_id in {None, "EMERGING"}:
        return "SERIOUS"
    return section_id


def council_line(items: Sequence[Mapping[str, Any]]) -> str:
    """One line under the bottom line: every Alert, Advisory and Insights in the window."""
    by_series: dict[str, list[str]] = {key: [] for key in SERIES}
    for item in items:
        series = item.get("series") or detect_series(
            item.get("title") or "", item.get("text") or "", item.get("url") or "", item.get("publisher") or "",
        )
        if series not in by_series:
            continue
        title = _plain(item.get("title") or "") or "(untitled)"
        if title not in by_series[series]:
            by_series[series].append(title)
    parts: list[str] = []
    for key in SERIES:
        label = SERIES_LABEL[key]
        titles = by_series[key]
        if not titles:
            parts.append(f"No {label} in this window")
        elif len(titles) == 1:
            parts.append(f"{label}: {titles[0]}")
        else:
            parts.append(f"{label} ({len(titles)}): " + "; ".join(titles[:5]) + ("…" if len(titles) > 5 else ""))
    return "Council bulletins — " + ". ".join(parts) + "."


def collapse_notes(notes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep one note per cluster; merge other sources onto the winner."""
    winners: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    unclustered: list[dict[str, Any]] = []
    for note in notes:
        sources = list(note.get("sources") or [])
        lead = sources[0] if sources else {}
        key = note.get("cluster_key") or cluster_key(
            note.get("title") or "",
            note.get("established") or "",
            series=note.get("bulletin_series"),
        )
        if not key:
            unclustered.append(dict(note))
            continue
        rank = authority_rank(
            lead.get("url") or "",
            lead.get("outlet") or "",
            str(note.get("decision") or "WATCH"),
        )
        evidence = -int((note.get("significance") or {}).get("evidence") or 0)
        score = (*rank, evidence)
        if key not in winners:
            winners[key] = dict(note)
            winners[key]["_score"] = score
            order.append(key)
            continue
        current = winners[key]
        if score < current.get("_score", (9, 9, 0)):
            merged_sources = list(note.get("sources") or [])
            for src in current.get("sources") or []:
                if src not in merged_sources:
                    merged_sources.append(src)
            winners[key] = {**dict(note), "sources": merged_sources, "_score": score}
        else:
            for src in sources:
                if src not in (current.get("sources") or []):
                    current.setdefault("sources", []).append(src)
    out: list[dict[str, Any]] = []
    for key in order:
        row = winners[key]
        row.pop("_score", None)
        out.append(row)
    out.extend(unclustered)
    return out
