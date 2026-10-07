"""Match a signal to the reader pack's named items. Pure: no I/O, no model, no score.

The reader pack names what one reader watches: projects, suppliers, customers, sites, natural hazards, standing conditions,
and open watchpoints. This index finds those names in a signal with the link ladder's discipline:

- a name counts when it is strong: in the title, in the lead, or named twice;
- a name followed by another capitalised word is a different, longer name: ``Chan Chun Sing`` is not the supplier ``Chan Chun``,
  and ``Leonardo DiCaprio`` is not ``Leonardo``. A corporate word (``Corp``, ``Electric``) or an acronym (``Leonardo DRS``) continues it;
- a supplier, customer, or site counts only when the text also has a Taiwan or rail cue. Without one, global news about the
  company is recorded as a passing mention, so the brief can say "normal-course global press only";
- a project alias that leaves out the city (``Purple Line``) needs the city in the text, so Maryland's Purple Line is not
  Kaohsiung's;
- an alias marked ``matchable: false`` in the register is never searched;
- a keyword shorter than six characters matches only as a standalone word, and only in its own case when it is an acronym.

A condition keyword says which standing condition a signal bears on. It never sets a trend. A hazard keyword needs a Taiwan cue.
A watchpoint needs one of its subject keywords strongly and one of its issue keywords anywhere in the page.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

LEAD_CHARS = 600
TAIWAN_CUES = ("Taiwan", "Taiwanese", "Taipei", "Kaohsiung", "Taichung", "Taoyuan", "Tainan", "Hsinchu", "Hualien", "Yilan", "Keelung")
RAIL_CUES = ("MRT", "metro", "rail", "railway", "train", "transit", "signalling", "signaling")

CORPORATE_WORDS = frozenset({
    "Co", "Co.", "Corp", "Corp.", "Corporation", "Company", "Ltd", "Ltd.", "Limited", "Inc", "Inc.", "Group", "Holdings", "Electric", "Technology", "Technologies",
    "Systems", "System", "Construction", "Engineering", "SpA", "S.p.A.", "S.p.A", "Line", "Lines", "Station", "Stations", "Extension", "Project", "Corridor",
})
PROJECT, SUPPLIER, CUSTOMER, SITE, HAZARD, CONDITION, WATCHPOINT = "PROJECT", "SUPPLIER", "CUSTOMER", "SITE", "HAZARD", "CONDITION", "WATCHPOINT"


@dataclass(frozen=True)
class ReaderHit:
    item_type: str
    item_id: str
    item_name: str
    matched: str
    where: str  # TITLE, LEAD, or BODY
    count: int
    strong: bool

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


def _is_acronym(alias: str) -> bool:
    return alias.isupper() and alias.replace("&", "").isalnum() and len(alias) <= 5


_TRANSIT_INFIX = r"[\s-]+(?:(?:MRT|Metro|Rapid\s+Transit|LRT)[\s-]+)?"


def _pattern(alias: str, *, transit: bool = False, exact_case: bool = False) -> re.Pattern[str]:
    """transit lets a project name carry an operator word, so 'Taichung Blue Line' also finds 'Taichung MRT Blue Line'.
    exact_case keeps a generic proper noun such as 'Yellow Line' from matching 'the yellow line' in prose."""
    joiner = _TRANSIT_INFIX if transit else r"[\s-]+"
    body = joiner.join(re.escape(part) for part in alias.split())
    return re.compile(rf"(?<![\w]){body}(?![\w])", 0 if _is_acronym(alias) or exact_case else re.IGNORECASE)


def _aliases(row: Mapping[str, Any], name_field: str) -> list[str]:
    names = [str(row.get(name_field) or "")]
    for alias in row.get("aliases") or []:
        if isinstance(alias, Mapping):
            if alias.get("matchable") is False:
                continue
            alias = alias.get("text") or ""
        names.append(str(alias))
    return [name for name in dict.fromkeys(name.strip() for name in names) if len(name) >= 2]


def _longer_name(text: str, end: int) -> bool:
    """True when the match runs straight into another capitalised word that is not a corporate word or an acronym."""
    following = re.match(r"\s+([A-Z][\w.&'-]*)", text[end:])
    if following is None:
        return False
    word = following.group(1)
    return word not in CORPORATE_WORDS and not word.isupper() and word.rstrip(".") not in CORPORATE_WORDS


def _usable_keyword(keyword: str) -> bool:
    return len(keyword) >= 6 or " " in keyword or "-" in keyword or _is_acronym(keyword)


class ReaderIndex:
    """Build once per reader pack. ``scan_plan`` may be None; then only the exposure register is searched."""

    def __init__(self, exposure: Mapping[str, Any], scan_plan: Mapping[str, Any] | None, baseline: Mapping[str, Any] | None = None, watchpoints: Iterable[Mapping[str, Any]] = ()) -> None:
        self.items: list[tuple[str, str, str, list[str], str | None]] = []  # type, id, name, aliases, the project's city
        for row in exposure.get("projects") or []:
            self.items.append((PROJECT, row["projectId"], row["projectName"], _aliases(row, "projectName"), row.get("city")))
        for row in exposure.get("suppliers") or []:
            self.items.append((SUPPLIER, row["supplierId"], row["supplierName"], _aliases(row, "supplierName"), None))
        for row in exposure.get("customers") or []:
            self.items.append((CUSTOMER, row["customerId"], row["customerName"], _aliases(row, "customerName"), None))
        for row in exposure.get("sites") or []:
            self.items.append((SITE, row["siteId"], row["siteName"], _aliases(row, "siteName")[1:], None))
        plan = scan_plan or {}
        for row in plan.get("customers") or []:
            if row.get("aliases"):
                self.items.append((CUSTOMER, row["itemId"], row.get("name") or row["itemId"], [str(a) for a in row["aliases"]], None))
        self.keyword_sets: list[tuple[str, str, str, list[str]]] = []
        names = {condition["id"]: condition["name"] for condition in (baseline or {}).get("conditions") or []}
        for row in plan.get("conditions") or []:
            self.keyword_sets.append((CONDITION, row["conditionId"], names.get(row["conditionId"], row["conditionId"]), [k for k in row.get("keywords") or [] if _usable_keyword(k)]))
        for row in plan.get("hazards") or []:
            self.keyword_sets.append((HAZARD, row["itemId"], row.get("name") or row["itemId"], [k for k in row.get("keywords") or [] if _usable_keyword(k)]))
        self.issue_words: dict[str, list[str]] = {}
        for row in watchpoints:
            raw = row.get("keywords") or []
            subject = raw.get("subject") if isinstance(raw, Mapping) else raw
            watchpoint_id = row["watchpoint_id"] if "watchpoint_id" in row else row["watchpointId"]
            self.issue_words[watchpoint_id] = list((raw.get("issue") if isinstance(raw, Mapping) else None) or row.get("issueKeywords") or [])
            self.keyword_sets.append((WATCHPOINT, watchpoint_id, row["title"], [k for k in subject or [] if _usable_keyword(k)]))
        self._patterns: dict[str, re.Pattern[str]] = {}

    def _find(self, alias: str, whole: str, title_end: int, *, transit: bool = False, exact_case: bool = False) -> tuple[str, int] | None:
        key = f"{transit}|{exact_case}|{alias}"
        pattern = self._patterns.get(key)
        if pattern is None:
            pattern = self._patterns[key] = _pattern(alias, transit=transit, exact_case=exact_case)
        starts = [match.start() for match in pattern.finditer(whole) if not _longer_name(whole, match.end())]
        if not starts:
            return None
        first = starts[0]
        where = "TITLE" if first < title_end else "LEAD" if first - title_end - 1 < LEAD_CHARS else "BODY"
        return where, len(starts)

    def hits(self, title: str, text: str) -> list[ReaderHit]:
        whole = f"{title}\n{text}"
        title_end = len(title)
        taiwan = any(re.search(rf"(?<![\w]){cue}(?![\w])", whole) for cue in TAIWAN_CUES)
        rail = any(re.search(rf"(?<![\w]){re.escape(cue)}(?![\w])", whole, 0 if cue.isupper() else re.IGNORECASE) for cue in RAIL_CUES)
        found: list[ReaderHit] = []
        for item_type, item_id, name, aliases, city in self.items:
            best: ReaderHit | None = None
            for alias in sorted(aliases, key=len, reverse=True):
                cityless = item_type == PROJECT and bool(city) and city.casefold() not in alias.casefold()
                place = self._find(alias, whole, title_end, transit=item_type == PROJECT, exact_case=cityless)
                if place is None:
                    continue
                where, count = place
                strong = where != "BODY" or count >= 2
                if item_type == PROJECT and city and city.casefold() not in alias.casefold() and not re.search(rf"(?<![\w]){re.escape(city)}(?![\w])", whole, re.IGNORECASE):
                    strong = False
                if item_type in {SUPPLIER, CUSTOMER, SITE} and not (taiwan or rail):
                    strong = False
                hit = ReaderHit(item_type, item_id, name, alias, where, count, strong)
                if best is None or (hit.strong, -("TITLE", "LEAD", "BODY").index(hit.where)) > (best.strong, -("TITLE", "LEAD", "BODY").index(best.where)):
                    best = hit
            if best is not None:
                found.append(best)
        for item_type, item_id, name, keywords in self.keyword_sets:
            matched: list[tuple[str, str, int]] = []
            for keyword in keywords:
                place = self._find(keyword, whole, title_end)
                if place is not None:
                    matched.append((keyword, *place))
            if not matched:
                continue
            matched.sort(key=lambda item: ("TITLE", "LEAD", "BODY").index(item[1]))
            keyword, where, count = matched[0]
            strong = where != "BODY" or count >= 2 or len(matched) >= 2
            if item_type == HAZARD and not taiwan:
                strong = False
            if item_type == WATCHPOINT and self.issue_words.get(item_id):
                issue = next((word for word in self.issue_words[item_id] if self._find(word, whole, title_end) is not None), None)
                strong = strong and issue is not None
                keyword = f"{keyword} + {issue}" if issue else keyword
            found.append(ReaderHit(item_type, item_id, name, keyword, where, sum(item[2] for item in matched), strong))
        return found


def strong_hits(hits: Iterable[ReaderHit], *types: str) -> list[ReaderHit]:
    return [hit for hit in hits if hit.strong and (not types or hit.item_type in types)]
