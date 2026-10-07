"""Ontology anchors: the short texts a candidate is compared with. Built from the registers, in a fixed order.

An anchor is a topic, never a name. Entity, customer, supplier, and competitor names are matched by the
deterministic bind, not by meaning.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any

MAX_SCOPE_TERMS = 25


@dataclass(frozen=True)
class Anchor:
    anchor_id: str
    kind: str
    text: str
    canonical_lob: str | None
    priority: str | None
    source: str

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


def _with_lob(name: str, lob: str | None) -> str:
    return f"{name} ({lob})" if lob else name


def _anchors(pack: Any) -> list[tuple[str, str, str | None, str | None, str]]:
    libraries = (pack.keyword_library or {}).get("libraries") or []
    for library in libraries:
        for tag in library["tagClusters"]:
            yield "TAG_CLUSTER", _with_lob(tag["name"], library["canonicalLob"]), library["canonicalLob"], tag["priority"], f"{library['sheet']} tagClusters"
    for library in libraries:
        for theme in library["emergingThemes"]:
            yield "EMERGING_THEME", _with_lob(theme, library["canonicalLob"]), library["canonicalLob"], None, f"{library['sheet']} emergingThemes"
    for library in libraries:
        terms = list(dict.fromkeys(term for group in library["tier1"] for term in group["terms"]))[:MAX_SCOPE_TERMS]
        if terms and library["canonicalLob"]:
            yield "LOB_SCOPE", f"{library['canonicalLob']}: " + "; ".join(terms), library["canonicalLob"], "Highest Priority", f"{library['sheet']} tier1"
    for theme in pack.themes.get("themes") or []:
        yield "STE_THEME", theme["themeName"], None, theme["tier"], f"URS-STE-THEMES {theme['themeId']}"
    management = pack.management_focus or {}
    for item in management.get("focusItems") or []:
        text = f"{item['focus']}. {item['whyItMatters']}" if item.get("whyItMatters") else item["focus"]
        yield "MANAGEMENT_FOCUS", text, item.get("canonicalLob"), None, f"focusItems {item['scope']}"
    for request in management.get("requests") or []:
        if not request["topics"]:
            yield "MANAGEMENT_REQUEST", request["text"], None, None, f"request {request['number']}"
            continue
        where = ", ".join(request["countries"])
        for topic in request["topics"]:
            yield "MANAGEMENT_REQUEST", f"{request['scope']} in {where}. {topic['name']}: " + "; ".join(topic["items"]), None, None, f"request {request['number']} {topic['name']}"


def build_anchors(pack: Any) -> list[Anchor]:
    seen: set[str] = set()
    anchors: list[Anchor] = []
    for kind, text, lob, priority, source in _anchors(pack):
        key = " ".join(text.split()).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
        anchors.append(Anchor(f"{kind}:{digest}", kind, " ".join(text.split()), lob, priority, source))
    return anchors
