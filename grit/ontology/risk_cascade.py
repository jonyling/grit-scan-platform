"""Place a signal in the three-level risk register: entity, business area, then Group. Pure: no I/O after loading.

Weight orders results only (ENTITY 3, BA 2, GROUP 1). It never decides whether a signal links to STE, and it never
multiplies a severity (invariant I10). A Group risk can be reached two ways, and each hit records which:

- KEYWORD: an approved Group keyword appears in the title or lead;
- CROSSWALK: a business-area theme that hit maps to the Group code. Every crosswalk row is PROPOSED until R&A approves it.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from grit.ontology.matchers.theme_matcher import theme_match
from grit.settings import CONFIG_DIR

LEAD_CHARS = 600


@dataclass(frozen=True)
class RiskHit:
    level: str
    weight: int
    risk_id: str
    name: str
    tier: str | None
    basis: str  # KEYWORD, THEME_KEYWORD, or CROSSWALK
    matched: str

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


def _boundary(keyword: str) -> re.Pattern[str]:
    body = r"\s+".join(re.escape(part) for part in keyword.split())
    return re.compile(rf"(?<![\w]){body}(?![\w])", re.IGNORECASE)


class RiskCascade:
    def __init__(self, cascade: Mapping[str, Any], entity_themes: Mapping[str, Any] | None, ba_themes: Mapping[str, Any] | None, group_keywords: Mapping[str, Any]) -> None:
        self.weights = {level["level"]: int(level["weight"]) for level in cascade.get("levels") or []}
        self.entity_themes = entity_themes
        self.ba_names = {theme["themeId"]: theme for theme in (ba_themes or {}).get("themes") or []}
        self.crosswalk: dict[str, list[str]] = {}
        self.entity_crosswalk: dict[str, list[str]] = {}
        for row in cascade.get("crosswalk") or []:
            self.crosswalk[row["baThemeId"]] = list(row.get("groupRiskCodes") or [])
            self.entity_crosswalk[row["entityThemeId"]] = list(row.get("groupRiskCodes") or [])
        self.group_names: dict[str, str] = {}
        self.group_patterns: list[tuple[str, str, re.Pattern[str]]] = []
        for risk in group_keywords.get("risks") or []:
            self.group_names[risk["riskCode"]] = risk["riskName"]
            for keyword in risk.get("keywords") or []:
                if keyword.get("status") == "APPROVED" and (len(keyword["text"]) >= 6 or " " in keyword["text"]):
                    self.group_patterns.append((risk["riskCode"], keyword["text"], _boundary(keyword["text"])))

    def hits(self, title: str, text: str, ba_theme_ids: Iterable[str] = ()) -> list[RiskHit]:
        lead = f"{title}\n{text[:LEAD_CHARS]}"
        found: list[RiskHit] = []
        if self.entity_themes:
            for theme in theme_match(lead, self.entity_themes)["matchedThemes"]:
                found.append(RiskHit("ENTITY", self.weights.get("ENTITY", 3), theme["themeId"], theme["themeName"], theme.get("tier"), "THEME_KEYWORD", theme.get("matchMethod", "KEYWORD_MATCH")))
        ba_ids = list(dict.fromkeys(ba_theme_ids))
        for theme_id in ba_ids:
            theme = self.ba_names.get(theme_id) or {}
            found.append(RiskHit("BA", self.weights.get("BA", 2), theme_id, str(theme.get("themeName") or theme_id), theme.get("tier"), "THEME_KEYWORD", "link theme hit"))
        seen: set[str] = set()
        folded = lead.casefold()
        for code, keyword, pattern in self.group_patterns:
            if code in seen or keyword.casefold() not in folded:
                continue
            if pattern.search(lead):
                seen.add(code)
                found.append(RiskHit("GROUP", self.weights.get("GROUP", 1), code, self.group_names[code], None, "KEYWORD", keyword))
        for theme_id in ba_ids:
            for code in self.crosswalk.get(theme_id, []):
                if code not in seen and code in self.group_names:
                    seen.add(code)
                    found.append(RiskHit("GROUP", self.weights.get("GROUP", 1), code, self.group_names[code], None, "CROSSWALK", f"{theme_id} (PROPOSED mapping)"))
        found.sort(key=lambda hit: (-hit.weight, hit.basis != "THEME_KEYWORD" and hit.basis != "KEYWORD", hit.risk_id))
        return found


def load_risk_cascade(config_dir: Path = CONFIG_DIR, pack: Any | None = None) -> RiskCascade:
    """Risk-Cascade.json names one entity and one business-area theme register (URS Taiwan and URS today). With a reader
    pack, a level's register is kept only when it belongs to the pack, its parent, or its children. Otherwise the pack's
    own themes fill its own level, and the other level is empty, so a DS&C signal is never placed in a URS theme."""
    cascade = json.loads((config_dir / "risk" / "Risk-Cascade.json").read_text(encoding="utf-8"))
    files = {level["level"]: config_dir / level["register"] for level in cascade["levels"]}
    load = lambda path: json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else None  # noqa: E731
    registers = {level: load(path) for level, path in files.items()}
    if pack is not None:
        lineage = {pack.pack, (pack.manifest or {}).get("parent"), *pack.children} - {None}
        for level in ("ENTITY", "BA"):
            try:
                owner = files[level].relative_to(config_dir).parts[0]
            except (KeyError, ValueError):
                continue
            if owner not in lineage:
                registers[level] = pack.themes if level == pack.level else None
    return RiskCascade(cascade, registers.get("ENTITY"), registers.get("BA"), registers.get("GROUP") or {})
